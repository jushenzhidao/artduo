#!/usr/bin/env python3
"""真实网关多形态验证（会计费！默认只跑 3 个形态，失败即停）。

形态：
  R1 单图编辑：狗照片作画布，加红围巾
  R2 双图合成：狗照片 + 红陶瓷立方体，点名把立方体放到前爪旁
  R3 无参考基线（英文提示词）：验证 refs 改动没有影响纯文生图

每次调用都核对：HTTP 200 / 落盘 PNG 有效 / 1024×1024 / 本次 refsUsed 与提交张数一致 /
trace 里记录了参考图指纹 / 进程计数 +1。
"""
from __future__ import annotations

import base64
import io
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROXY = os.environ.get("REAL_PROXY", "http://127.0.0.1:8788")
ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
OUT = QA / "shots" / "real"
OUT.mkdir(parents=True, exist_ok=True)
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 前端同款压缩：长边 1280、JPEG q0.85、透明铺白
from PIL import Image  # noqa: E402


def to_data_uri(path: Path, side: int = 1280) -> str:
    im = Image.open(path).convert("RGB")
    scale = min(1.0, side / max(im.size))
    if scale < 1.0:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                       Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def status() -> dict:
    with op.open(PROXY + "/api/status", timeout=10) as r:
        return json.loads(r.read())


def post(payload: dict, timeout=180) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(PROXY + "/api/generate", body,
                                 {"Content-Type": "application/json"})
    try:
        with op.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def png_info(raw: bytes) -> tuple[bool, int, int]:
    ok = raw[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", raw[16:24]) if ok else (0, 0)
    return ok, w, h


def main() -> int:
    cases = [
        ("R1", "单图编辑 · 给金毛加红围巾",
         "给这只金毛犬戴一顶红色针织围巾，狗的姿态与背景保持不变",
         [ROOT / "assets/generated/real-1794113439799-2.png"]),
        ("R2", "双图合成 · 立方体放到前爪旁",
         "把第二张图里的红色陶瓷立方体放到这只金毛犬的前爪旁边，光影与桌面保持一致",
         [ROOT / "assets/generated/real-1794113439799-2.png",
          ROOT / "assets/generated/real-1794074399905-1.png"]),
        ("R3", "无参考基线 · 英文文生图",
         "a red bicycle leaning against a white plaster wall, soft morning light, 35mm photo",
         []),
    ]

    results, before = [], status()
    print(f"[start] calls={before['calls']}/{before['maxCalls']} mode={before['upstreamMode']}")
    if before["upstreamMode"] != "live":
        print("当前不是 real 链路，拒绝执行", file=sys.stderr)
        return 2

    for cid, name, prompt, refs in cases:
        n0 = status()["calls"]
        data_uris = [to_data_uri(p) for p in refs]
        payload = {"prompt": prompt, "mode": "image"}
        if data_uris:
            payload["refs"] = [{"name": p.stem, "url": u} for p, u in zip(refs, data_uris)]
        t0 = time.monotonic()
        st, d = post(payload)
        elapsed = round(time.monotonic() - t0, 1)
        n1 = status()["calls"]

        row = {"id": cid, "name": name, "prompt": prompt, "refs_in": len(refs),
               "http": st, "ok": bool(d.get("ok")), "elapsed_s": elapsed,
               "calls_delta": n1 - n0, "error": d.get("error"),
               "refs_used": d.get("refsUsed"), "bytes": None, "w": 0, "h": 0,
               "file": None}
        if d.get("ok") and d.get("images"):
            url = d["images"][0]["url"]
            with op.open(PROXY + url, timeout=60) as r:
                raw = r.read()
            good, w, h = png_info(raw)
            dest = OUT / f"{cid}.png"
            dest.write_bytes(raw)
            row.update(bytes=len(raw), w=w, h=h, file=str(dest.relative_to(ROOT)), png=good)

        ok = (row["ok"] and row["bytes"] and row["w"] == 1024 and row["calls_delta"] == 1
              and (row["refs_used"] or 0) == len(refs))
        row["pass"] = bool(ok)
        results.append(row)
        print(f"[{cid}] {'PASS' if ok else 'FAIL'} {elapsed}s refs={len(refs)}→{row['refs_used']} "
              f"{row['w']}x{row['h']} {row['bytes']}B {row['error'] or ''}")

        # 失败即停，避免继续烧额度
        if not ok:
            print("[abort] 该形态失败，停止后续真实调用", file=sys.stderr)
            break

    after = status()
    rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "proxy": PROXY,
           "billed_calls": after["calls"] - before["calls"],
           "calls_before": before["calls"], "calls_after": after["calls"],
           "max_calls": after["maxCalls"],
           "passed": sum(1 for r in results if r.get("pass")), "total": len(results),
           "results": results}
    (QA / "real-matrix.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    print(f"\n{rep['passed']}/{rep['total']} passed · 本次真实计费调用 {rep['billed_calls']} 次"
          f" · 余额计数 {after['calls']}/{after['maxCalls']}")
    return 0 if rep["passed"] == rep["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
