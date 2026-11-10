#!/usr/bin/env python3
"""云端拼接（reel.1task.cn）自检：证明公网服务真的在跑，且前端的「云端」开关可用。

不需要起本地容器：直接打线上服务，素材用本地已有的 mock 镜头视频（几百 KB）。
云侧只花 CPU（不计费），但会走真实公网传输。

  R1 /v1/healthz 200 且字段齐全（ffmpeg / workers / limits / uploads）
  R2 无 key 提交 → 401
  R3 域名白名单：非白名单 URL → 400
  R4 直传 PUT /v1/blob/<id>.mp4 → 201，且返回字节数一致
  R5 提交 → 轮询 → succeeded，成片 704×1248、时长与段数吻合
  R6 成片可下载（Content-Type: video/mp4）
  R7 本机代理 /api/reel {cloud:true} → via=cloud，且成片落回本地 assets
  R8 页面切「云端」后刷新仍保留（存档）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import video_check
from video_check import B

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
SHOTS = QA / "shots" / "cloud"
SHOTS.mkdir(parents=True, exist_ok=True)
video_check.SHOTS = SHOTS
REPORT = QA / "cloud-reel-report.json"

CLOUD = os.environ.get("REEL_CLOUD", "https://reel.1task.cn")
KEY_FILE = QA / "reel-cloud-key.txt"
PROXY = "http://127.0.0.1:8796"          # 视频 mock 实例（出片快、零计费）
SEG1 = ROOT / "assets/generated/vid-cgt-mock-1.mp4"
SEG2 = ROOT / "assets/generated/vid-cgt-mock-13.mp4"


def key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip() if KEY_FILE.exists() else ""


def call(method: str, path: str, body: bytes | None = None, headers: dict | None = None,
         timeout: int = 120) -> tuple[int, dict]:
    hdrs = dict(headers or {})
    if key():
        hdrs.setdefault("X-Reel-Key", key())
    req = urllib.request.Request(CLOUD + path, data=body, method=method, headers=hdrs)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else {"bytes": len(raw)})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw[:120].decode("utf-8", "replace")}


def main() -> int:
    results = []

    def chk(name, got, want):
        ok = got == want
        results.append({"name": name, "got": got, "want": want, "ok": ok})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {got!r} (期望 {want!r})")

    code, health = call("GET", "/v1/healthz", timeout=20)
    chk("R1 线上健康 200", code, 200)
    chk("R1 服务名", health.get("service"), "reel-service")
    chk("R1 带 ffmpeg 版本", "ffmpeg version" in (health.get("ffmpeg") or ""), True)
    chk("R1 支持直传", health.get("uploads"), True)
    chk("R1 规格 704×1248", (health.get("limits") or {}).get("size"), [704, 1248])

    code, _ = call("POST", "/v1/reel", json.dumps({"segments": []}).encode(),
                   {"Content-Type": "application/json"}, timeout=20)
    # 空 segments 会被 400 拦；无 key 的用例单独发
    req = urllib.request.Request(CLOUD + "/v1/reel", data=b'{"segments":[{"url":"https://a/b.mp4"}]}',
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as r:
            unauth = r.status
    except urllib.error.HTTPError as exc:
        unauth = exc.code
    chk("R2 无 key 提交 401", unauth, 401)

    code, body = call("POST", "/v1/reel",
                      json.dumps({"segments": [{"url": "https://evil.example.com/x.mp4"}]}).encode(),
                      {"Content-Type": "application/json"}, timeout=20)
    chk("R3 非白名单域名 400", code, 400)
    chk("R3 错误里点名白名单", "白名单" in (body.get("error") or ""), True)

    if not (SEG1.exists() and SEG2.exists()):
        raise RuntimeError("缺少测试素材（assets/generated/vid-cgt-mock-*.mp4），先跑 tools/video_check.py")

    ups = []
    for p in (SEG1, SEG2):
        name = f"chk{int(time.time() * 1000) % 10**10}{len(ups)}.mp4"
        code, body = call("PUT", f"/v1/blob/{name}", p.read_bytes(),
                          {"Content-Type": "video/mp4"}, timeout=120)
        chk(f"R4 直传 {p.name}", (code, body.get("bytes")), (201, p.stat().st_size))
        ups.append(name)

    payload = {"segments": [{"upload": u} for u in ups],
               "options": {"transition": 0.3, "subtitles": True, "titles": True, "music": "off"},
               "title": "云端自检", "lines": ["第一镜台词", "第二镜音效"]}
    code, body = call("POST", "/v1/reel", json.dumps(payload).encode(),
                      {"Content-Type": "application/json"}, timeout=30)
    chk("R5 受理 202", code, 202)
    job = body.get("id")
    final = {}
    t0 = time.time()
    while time.time() - t0 < 180:
        time.sleep(1)
        _, final = call("GET", f"/v1/reel/{job}", timeout=20)
        if final.get("status") in ("succeeded", "failed"):
            break
    chk("R5 拼成功", final.get("status"), "succeeded")
    chk("R5 段数", final.get("segments"), 2)
    # 1.8 片头 + 2 × 2s + 1.8 片尾 − 3 × 0.3 转场 = 6.7s
    chk("R5 时长含片头片尾与转场", 6.2 <= float(final.get("seconds") or 0) <= 7.2, True)
    url = final.get("url") or ""
    chk("R5 成片地址在公网", url.startswith(CLOUD + "/files/"), True)
    print(f"    （云端 ffmpeg 耗时 {final.get('elapsed_ms')}ms，地址 {url}）")

    req = urllib.request.Request(url)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=60) as r:
            blob = r.read()
            ctype = r.headers.get("Content-Type")
        chk("R6 成片可下载", (len(blob) > 100_000, ctype), (True, "video/mp4"))
    except Exception as exc:
        chk("R6 成片可下载", f"{type(exc).__name__}", "video/mp4")

    # ---- R7 走本机代理（前端实际用的路径）----
    if not PROXY:
        raise RuntimeError("需要 8796（视频 mock 实例）在跑")
    body = {"files": [f"/assets/generated/{SEG1.name}", f"/assets/generated/{SEG2.name}"],
            "ep": 1, "cloud": True, "options": {"transition": 0.3, "subtitles": True},
            "title": "代理侧自检", "lines": ["A", "B"]}
    req = urllib.request.Request(PROXY + "/api/reel", data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=300) as r:
        d = json.loads(r.read())
    chk("R7 代理返回 via=cloud", d.get("via"), "cloud")
    chk("R7 成片落回本地", str(d.get("url")).startswith("/assets/generated/reel-ep1-"), True)
    local = ROOT / str(d.get("url")).lstrip("/")
    chk("R7 本地文件存在", local.exists() and local.stat().st_size > 10_000, True)
    print(f"    （上传+云端+回传 {round(time.time() - t0, 1)}s，云端 ffmpeg {d.get('cloud', {}).get('elapsed_ms')}ms）")

    # ---- R8 页面开关 ----
    b = B()
    try:
        b.open(PROXY, "r8", clear=True)
        b.make_script()
        b.js("document.querySelector('#sbGear').click()")
        time.sleep(0.5)
        chk("R8 云端按钮可用", b.ev("document.querySelector('[data-reelmode=\"cloud\"]').disabled"), False)
        b.js("document.querySelector('[data-reelmode=\"cloud\"]').click()")
        time.sleep(0.8)
        chk("R8 切到云端", b.evj("window.__qaStoryboard().reelMode"), "cloud")
        b.open(PROXY, "r8b", clear=False)
        chk("R8 刷新后仍保留", b.evj("window.__qaStoryboard().reelMode"), "cloud")
    finally:
        subprocess.run(["bsk", "session", "stop", "--session", b.s, "--json"], capture_output=True)

    ok = sum(1 for r in results if r["ok"])
    REPORT.write_text(json.dumps({"at": time.strftime("%F %T"), "cloud": CLOUD, "passed": ok,
                                  "total": len(results), "items": results},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{ok}/{len(results)} passed -> {REPORT.relative_to(ROOT)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
