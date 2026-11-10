#!/usr/bin/env python3
"""代理层对抗审查：UI 矩阵覆盖不到的边界。

只打本机代理（上游是 127.0.0.1 的 mock），全程零计费。
每个探针都读 mock 上游计数器，用「上游是否真的被调用」判断守卫是否生效 ——
只看 HTTP 状态码是不够的：4xx 也可能是「先转发再报错」。
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
OUT = QA / "adversarial.json"
HOST, PORT = "127.0.0.1", 8788
MOCK = "http://127.0.0.1:8799"
CAP_PORT = 8789


def upstream_count() -> int:
    try:
        c = http.client.HTTPConnection("127.0.0.1", 8799, timeout=3)
        c.request("GET", "/__stats")
        return int(json.loads(c.getresponse().read()).get("requests", -1))
    except Exception:
        return -1


def req(method: str, path: str, body: bytes | None = None, port: int = PORT,
        headers: dict | None = None):
    """用 http.client 直接发原始 path，避免客户端把 ../ 规范化掉。"""
    c = http.client.HTTPConnection(HOST, port, timeout=20)
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def last_upstream_prompt() -> str:
    """读 mock 上游最后一条记录里的 prompt，用来核对规范化/截断的真实结果。"""
    f = QA / "mock-upstream.jsonl"
    if not f.exists():
        return ""
    last = [x for x in f.read_text(encoding="utf-8").splitlines() if x.strip()][-1]
    try:
        return ((json.loads(last).get("body") or {}).get("prompt")) or ""
    except json.JSONDecodeError:
        return ""

def probe(name: str, expect: str, fn) -> dict:
    before = upstream_count()
    try:
        status, data, extra = fn()
        err = None
    except Exception as exc:
        status, data, extra, err = -1, b"", {}, f"{type(exc).__name__}: {exc}"
    after = upstream_count()
    text = data.decode("utf-8", "replace")
    rec = {
        "name": name, "expect": expect, "status": status,
        "upstream_delta": after - before if after >= 0 and before >= 0 else None,
        "body_head": text[:180], "error": err, **extra,
    }
    return rec


def main() -> int:
    QA.mkdir(parents=True, exist_ok=True)
    results = []

    # ---- 路由与方法 ----
    results.append(probe("GET /api/generate 应拒绝", "非 200 且不打上游",
                         lambda: (*req("GET", "/api/generate"), {})))
    results.append(probe("POST /api/unknown 应 404", "404",
                         lambda: (*req("POST", "/api/unknown", b"{}"), {})))
    results.append(probe("PUT /api/generate 应拒绝", "非 200",
                         lambda: (*req("PUT", "/api/generate", b"{}"), {})))

    # ---- 请求体守卫 ----
    results.append(probe("非法 JSON", "400 且不打上游",
                         lambda: (*req("POST", "/api/generate", b"{not json"), {})))
    results.append(probe("空对象", "400 且不打上游",
                         lambda: (*req("POST", "/api/generate", b"{}"), {})))
    results.append(probe("纯空白提示词", "400 且不打上游",
                         lambda: (*req("POST", "/api/generate",
                                       json.dumps({"prompt": "   \n\t "}).encode()), {})))
    results.append(probe("非字符串提示词", "400 且不打上游",
                         lambda: (*req("POST", "/api/generate",
                                       json.dumps({"prompt": {"a": 1}}).encode()), {})))
    results.append(probe("mode=video 应拒绝", "400 且不打上游",
                         lambda: (*req("POST", "/api/generate",
                                       json.dumps({"prompt": "测试", "mode": "video"}).encode()), {})))
    big = json.dumps({"prompt": "x" * (70 * 1024)}).encode()
    results.append(probe("70KB 请求体", "413 且不打上游",
                         lambda: (*req("POST", "/api/generate", big), {})))

    # ---- 提示词规范化（不是「原样透传」，而是确定性规则）----
    raw = '  前导空格\n\n\n\n三行空行被折叠  "引号" \'单引号\' 🌧️  '
    r_norm = probe("规范化：去空白 + 折叠空行", "200，上游收到规范化后的文本",
                   lambda: (*req("POST", "/api/generate",
                                 json.dumps({"prompt": raw}, ensure_ascii=False).encode()), {}))
    import re as _re
    expect_norm = _re.sub(r"\n{3,}", "\n\n", raw.strip())
    r_norm["observed_prompt"] = last_upstream_prompt()
    r_norm["ok"] = r_norm["status"] == 200 and r_norm["observed_prompt"] == expect_norm
    results.append(r_norm)
    long_p = "赛" * 2500
    r_trunc = probe("2500 字应截断到 2000", "200，上游 prompt 长度 2000",
                    lambda: (*req("POST", "/api/generate",
                                  json.dumps({"prompt": long_p}, ensure_ascii=False).encode()), {}))
    r_trunc["observed_len"] = len(last_upstream_prompt())
    r_trunc["ok"] = r_trunc["status"] == 200 and r_trunc["observed_len"] == 2000
    results.append(r_trunc)

    # ---- 路径穿越 ----
    results.append(probe("路径穿越 /../../etc/passwd", "非 200 且无 root:",
                         lambda: (*req("GET", "/../../../../etc/passwd"), {})))
    results.append(probe("编码路径穿越 %2e%2e", "非 200 且无 root:",
                         lambda: (*req("GET", "/%2e%2e/%2e%2e/%2e%2e/etc/passwd"), {})))
    results.append(probe("读取 .env", "非 200",
                         lambda: (*req("GET", "/../bfl2openai/.env"), {})))

    # ---- 调用上限（独立端口，避免污染主进程计数）----
    cap = {"name": "调用上限 JIMENG_MAX_CALLS=1", "expect": "第 1 次 200、第 2 次 429、上游只增 1"}
    env = dict(os.environ, PORT=str(CAP_PORT), JIMENG_MAX_CALLS="1",
               BFL_BASE=MOCK, BFL_KEY_FILE=str(QA / "mock-key.env"))
    proc = subprocess.Popen([sys.executable, str(ROOT / "server.py")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                s = socket.create_connection((HOST, CAP_PORT), timeout=0.2)
                s.close()
                break
            except OSError:
                time.sleep(0.1)
        before = upstream_count()
        s1, d1 = req("POST", "/api/generate", json.dumps({"prompt": "上限测试 A"}).encode(), port=CAP_PORT)
        s2, d2 = req("POST", "/api/generate", json.dumps({"prompt": "上限测试 B"}).encode(), port=CAP_PORT)
        after = upstream_count()
        cap.update(status=f"{s1}/{s2}", upstream_delta=after - before,
                   body_head=f"1st={d1.decode('utf-8','replace')[:60]} | 2nd={d2.decode('utf-8','replace')[:90]}",
                   ok=(s1 == 200 and s2 == 429 and (after - before) == 1))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    results.append(cap)

    # ---- 响应头与密钥泄漏 ----
    c = http.client.HTTPConnection(HOST, PORT, timeout=10)
    c.request("GET", "/api/status")
    r = c.getresponse()
    status_headers = dict(r.getheaders())
    status_body = r.read().decode("utf-8", "replace")
    c.close()
    results.append({
        "name": "/api/status 不得回显密钥", "expect": "无 key 字段值",
        "status": 200,
        "body_head": status_body[:200],
        "ok": "MOCK-LOCAL-ONLY" not in status_body and "Authorization" not in status_body,
    })
    results.append({
        "name": "静态响应头不得带 Server 版本泄漏之外的敏感项", "expect": "无 Set-Cookie",
        "status": 200, "body_head": json.dumps({k: v for k, v in status_headers.items()
                                                if k.lower() in ("cache-control", "x-qa-build", "set-cookie")}),
        "ok": "set-cookie" not in {k.lower() for k in status_headers},
    })

    # ---- 判定 ----
    for r in results:
        if "ok" in r:
            continue
        s, d = r["status"], r["body_head"]
        name = r["name"]
        if name.startswith("GET /api/generate") or name.startswith("PUT"):
            ok = s != 200 and r["upstream_delta"] == 0
        elif "404" in r["expect"]:
            ok = s == 404 and r["upstream_delta"] == 0
        elif "400" in r["expect"]:
            ok = s == 400 and r["upstream_delta"] == 0
        elif "413" in r["expect"]:
            ok = s == 413 and r["upstream_delta"] == 0
        elif name.startswith("规范化"):
            ok = s == 200 and r["upstream_delta"] == 1
        elif name.startswith("2500"):
            ok = s == 200 and r["upstream_delta"] == 1
        elif name.startswith("路径穿越") or name.startswith("编码"):
            ok = s != 200 and "root:" not in d
        elif name.startswith("读取 .env"):
            ok = s != 200
        else:
            ok = False
        r["ok"] = ok

    passed = sum(1 for r in results if r.get("ok"))
    summary = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "total": len(results),
               "passed": passed, "new_billed_calls": 0, "probes": results}
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        flag = "PASS" if r.get("ok") else "FAIL"
        print(f"  {flag} {r['name']} -> {r['status']} (上游Δ{r.get('upstream_delta')})")
    print(f"\n[adv] {passed}/{len(results)} PASS -> {OUT}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
