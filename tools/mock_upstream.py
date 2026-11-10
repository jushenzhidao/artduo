#!/usr/bin/env python3
"""零计费、可观测、可注入故障的 OpenAI 兼容图像上游。

存在意义（第一性原理）：多形态端到端要验证的是「浏览器→代理→上游→产物→渲染」
这条链路的行为不变式，而不是反复向计费网关买同一张图。把上游换成本进程后：
  - HTTP 链路、请求体构造、base64 解码、PNG 落盘、页面渲染全部照常真实发生；
  - 每个到达的请求被逐条记录（L3 抓包），可与浏览器侧(L1)、代理侧(L2)对账；
  - 通过提示词前缀注入 502 / 非 JSON / URL / 空 data / 坏 base64 / 慢响应，
    验证失败是否被如实上报、有无静默重试或降级为演示结果。

只用标准库，仅监听 127.0.0.1，绝不访问公网。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import struct
import sys
import threading
import time
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
RECORD = Path(os.environ.get("MOCK_RECORD", str(QA / "mock-upstream.jsonl")))
PORT = int(os.environ.get("MOCK_PORT", "8799"))
# 所有请求统一延迟（毫秒）：并发用例靠它把「串行 6×d」和「3 路并发 ≈ 2×d」区分开
DELAY_MS = int(os.environ.get("MOCK_DELAY_MS", "0"))
# 前 N 次失败、之后成功：用来验证「重试 + 抖动」是否真的把任务救回来了
FLAKY_FAILS = int(os.environ.get("MOCK_FLAKY_FAILS", "2"))
_flaky_hits: dict[str, int] = {}

# ---- new-api 兼容的额度端点（只读、不花钱）----
# 默认 $1 总额度、每张成功出图扣 3.8 美分（= 38 积分，按 1 USD = 1000 积分）。
# 需要验证「余额不足」时可用 /__billing/total 把总额调小。
BILLING_ON = os.environ.get("MOCK_BILLING", "0") != "0"
BILLING_TOTAL_USD = float(os.environ.get("MOCK_BILLING_TOTAL_USD", "1") or 1)
BILLING_PRICE_CENTS = float(os.environ.get("MOCK_BILLING_PRICE_CENTS", "3.8") or 3.8)
_bill = {"used_cents": 0.0}
_bill_lock = threading.Lock()

# 故障场景：提示词里出现该标记即触发对应响应
SCENARIOS = {
    "__MOCK_502__": "http_502",
    "__MOCK_NONJSON__": "non_json",
    "__MOCK_URL__": "url_only",
    "__MOCK_EMPTY__": "empty_data",
    "__MOCK_BADB64__": "bad_base64",
    "__MOCK_SLOW__": "slow_ok",
    "__MOCK_REQUIRE_REFS__": "require_refs",
    "__MOCK_500__": "http_500",
    "__MOCK_400__": "http_400",
    "__MOCK_FLAKY__": "flaky",
}


def make_png(seed: int, size: int = 64) -> bytes:
    """构造一张确定性噪声 PNG（压缩后必然 > 1024 字节，满足代理的最小尺寸校验）。"""
    rnd = random.Random(seed)
    r = 60 + (seed % 120)
    g = 40 + (seed // 7 % 140)
    b = 60 + (seed // 13 % 150)
    raw = bytearray()
    for _ in range(size):
        raw.append(0)  # filter type 0
        for _ in range(size):
            raw += bytes((min(255, r + rnd.randint(-30, 30)),
                          min(255, g + rnd.randint(-30, 30)),
                          min(255, b + rnd.randint(-30, 30)), 255))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def redact(headers) -> dict:
    """头信息脱敏：Authorization 只留指纹，绝不明文落盘。"""
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk == "authorization":
            out[k] = "Bearer sha256:" + hashlib.sha256(v.encode()).hexdigest()[:12]
        elif lk in ("cookie", "set-cookie", "x-api-key"):
            out[k] = "<redacted>"
        else:
            out[k] = v
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mock-bfl/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("MOCK_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, status: int, payload: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _billing_state(self) -> dict:
        with _bill_lock:
            used = round(_bill["used_cents"], 6)
        return {"enabled": BILLING_ON, "total_usd": BILLING_TOTAL_USD,
                "used_cents": used, "price_cents": BILLING_PRICE_CENTS,
                "remaining_cents": round(BILLING_TOTAL_USD * 100 - used, 6)}

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/__stats":
            n = 0
            if RECORD.exists():
                n = len([x for x in RECORD.read_text(encoding="utf-8").splitlines() if x.strip()])
            return self._json(200, {"ok": True, "requests": n, "mock": True,
                                    "billing": self._billing_state()})
        if path == "/__billing":
            return self._json(200, {"ok": True, **self._billing_state()})
        if path in ("/v1/dashboard/billing/subscription", "/dashboard/billing/subscription"):
            if not BILLING_ON:
                return self._json(404, {"error": "billing disabled (mock)"})
            total = BILLING_TOTAL_USD
            return self._json(200, {
                "object": "billing_subscription", "has_payment_method": True,
                "soft_limit_usd": total, "hard_limit_usd": total,
                "system_hard_limit_usd": total, "access_until": 0,
            })
        if path in ("/v1/dashboard/billing/usage", "/dashboard/billing/usage"):
            if not BILLING_ON:
                return self._json(404, {"error": "billing disabled (mock)"})
            return self._json(200, {"object": "list", "total_usage": round(_bill["used_cents"], 6)})
        if path == "/v1/models":
            return self._json(200, {"data": [{"id": "flux-3-image"}, {"id": "mock"}], "object": "list"})
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/__billing/reset":
            with _bill_lock:
                _bill["used_cents"] = 0.0
            return self._json(200, {"ok": True, **self._billing_state()})
        if path == "/__billing/total":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                body = {}
            global BILLING_TOTAL_USD
            try:
                BILLING_TOTAL_USD = float(body.get("total_usd"))
            except Exception:
                return self._json(400, {"error": "total_usd must be a number"})
            return self._json(200, {"ok": True, **self._billing_state()})
        if path != "/v1/images/generations":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            parsed = json.loads(body.decode("utf-8"))
        except Exception:
            parsed = {"__unparsable__": body.decode("utf-8", "replace")[:200]}
        prompt = parsed.get("prompt", "") if isinstance(parsed, dict) else ""

        # 参考图只留数量/大小/指纹：整段 base64 落盘会让证据文件不可用
        raws = parsed.get("image") if isinstance(parsed, dict) else None
        raws = [] if raws is None else (raws if isinstance(raws, list) else [raws])
        ref_meta = []
        for u in raws:
            try:
                blob = base64.b64decode(str(u).split(",", 1)[1])
            except Exception:
                blob = b""
            ref_meta.append({"bytes": len(blob),
                             "sha256": hashlib.sha256(blob).hexdigest()[:16] if blob else None})
        recorded = dict(parsed) if isinstance(parsed, dict) else parsed
        if isinstance(recorded, dict) and raws:
            recorded["image"] = f"<{len(raws)} images omitted>"

        scenario = "ok"
        for marker, name in SCENARIOS.items():
            if marker in str(prompt):
                scenario = name
                break

        QA.mkdir(parents=True, exist_ok=True)
        with RECORD.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "method": "POST", "path": self.path,
                "headers": redact(self.headers), "body": recorded, "body_bytes": len(body),
                "scenario": scenario, "refs_count": len(raws), "refs": ref_meta,
            }, ensure_ascii=False) + "\n")

        if DELAY_MS > 0:
            time.sleep(DELAY_MS / 1000.0)

        if scenario == "flaky":
            key = str(prompt)[:80]
            _flaky_hits[key] = _flaky_hits.get(key, 0) + 1
            if _flaky_hits[key] <= FLAKY_FAILS:
                return self._json(503, {"error": {"message": f"mock flaky {_flaky_hits[key]}/{FLAKY_FAILS}"}})
            scenario = "ok"
        if scenario == "http_400":
            return self._json(400, {"error": {"message": "mock bad request (should not retry)"}})
        if scenario == "http_502":
            return self._json(502, {"error": {"message": "The image service is busy (mock 502)"}})
        if scenario == "http_500":
            return self._json(500, {"detail": "mock upstream internal error"})
        if scenario == "non_json":
            return self._send(200, b"<html>mock broken payload</html>", "text/html")
        if scenario == "url_only":
            return self._json(200, {"data": [{"url": "https://example.invalid/x.png"}]})
        if scenario == "require_refs" and not raws:
            return self._json(400, {"error": {"message": "image[] is required (mock)"}})
        if scenario == "empty_data":
            return self._json(200, {"data": []})
        if scenario == "bad_base64":
            return self._json(200, {"data": [{"b64_json": "!!!not-base64!!!"}]})
        if scenario == "slow_ok":
            time.sleep(2.0)

        digest = int(hashlib.sha256(str(prompt).encode()).hexdigest()[:8], 16)
        png = make_png(digest)
        # 只有真正成功出图才记账：失败场景不扣额度，便于断言释放逻辑
        if BILLING_ON:
            with _bill_lock:
                _bill["used_cents"] += BILLING_PRICE_CENTS
        return self._json(200, {
            "created": int(time.time()),
            "data": [{"b64_json": base64.b64encode(png).decode()}],
            "usage": {"mock": True, "refs": len(raws),
                      "prompt_sha256": hashlib.sha256(str(prompt).encode()).hexdigest()[:16]},
        })


def main() -> int:
    QA.mkdir(parents=True, exist_ok=True)
    print(f"[mock-upstream] http://127.0.0.1:{PORT} record={RECORD}", flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
