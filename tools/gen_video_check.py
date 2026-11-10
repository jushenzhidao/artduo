#!/usr/bin/env python3
"""生成页「视频生成」接真实链路自检（bsk 驱动 + mock avm，零计费）。

证伪的不是「代码跑没跑」，而是这几条不变式：
  G1  链路真实时，页面不能再自称演示（徽标/提示/积分三处口径一致）
  G2  参数条只暴露真实档位；计费档在未开启时不可选（点了也不生效）
  G3  无首帧 = 文生视频：请求体里不能有 image_url，ratio 用页面选的
  G4  有首帧 = 图生视频：first_frame + adaptive + 首帧裁成 9:16
  G5  一次提交只对应上游一次 create（连点不放大）
  G6  提交后立刻可继续操作（不锁页面 2 分钟），卡片自己轮询
  G7  出片后是 <video> 且能解码，落盘在 assets/generated/
  G8  失败必须说人话（上游原因透传），不出现"假成片"
  G9  刷新不丢成片；running 会话刷新后如实标中断
  G10 未配置 cookie 的实例：徽标/提示如实，且不产生任何上游请求
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
SHOTS = QA / "shots" / "genvideo"
SHOTS.mkdir(parents=True, exist_ok=True)
RECORD = QA / "mock-avm.jsonl"
REPORT = QA / "genvideo-report.json"
BROWSER = "90d43dd3"

MOCK_PORT = 8795
MAIN_PORT = 8884
NOAVM_PORT = 8885

passes: list[str] = []
fails: list[str] = []
B_SESSION: str | None = None


def chk(name: str, got, want=True) -> bool:
    ok = got == want
    (passes if ok else fails).append(name if ok else f"{name} · got={got!r} want={want!r}")
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"\n        got={got!r} want={want!r}"), flush=True)
    return ok


def sh(*args, timeout=120) -> str:
    r = subprocess.run(["bsk", *args], capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip()


def jl(raw: str) -> dict:
    try:
        return json.loads(raw, strict=False)
    except Exception:
        return {"__raw": raw[:300]}


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_json(url: str, timeout=8):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wait_http(port: int, tries=60) -> bool:
    for _ in range(tries):
        try:
            http_json(f"http://127.0.0.1:{port}/api/status")
            return True
        except Exception:
            time.sleep(0.25)
    return False


def records() -> list[dict]:
    if not RECORD.exists():
        return []
    out = []
    for line in RECORD.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def rec_since(n: int, marker: str) -> list[dict]:
    return [r for r in records()[n:] if marker in (r.get("prompt") or "")]


PROCS: list[subprocess.Popen] = []


def spawn(port: int, extra: dict) -> subprocess.Popen:
    env = dict(os.environ)
    env.update({
        "PORT": str(port), "JIMENG_LLM": "off", "JIMENG_MAX_CALLS": "0",
        "JIMENG_MAX_VIDEOS": "30", "AVM_MINT": "off",
        "AVM_BASE": f"http://127.0.0.1:{MOCK_PORT}",
    })
    env.update(extra)
    p = subprocess.Popen([sys.executable, "server.py"], cwd=str(ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    PROCS.append(p)
    if not wait_http(port):
        raise RuntimeError(f"实例 {port} 起不来")
    return p


class B:
    def __init__(self):
        d = jl(sh("session", "start", "--json", "--browser", BROWSER,
                 "--width", "1500", "--height", "1000", "--no-focus", "--name", "genvideo"))
        self.s = d.get("session_id")
        if not self.s:
            raise RuntimeError(f"session start 失败: {d}")
        self.tab = jl(sh("tab", "create", "--session", self.s, "--url", "about:blank", "--json")).get("tab_id")
        global B_SESSION
        B_SESSION = self.s

    def sync(self, tries=14):
        last = {}
        for _ in range(tries):
            last = jl(sh("cooperate", "sync", "--session", self.s, "--policy", "target", "--json"))
            if last.get("context_id") and last.get("observation_id"):
                return last["context_id"], last["observation_id"]
            time.sleep(0.35)
        raise RuntimeError(f"sync 失败: {json.dumps(last, ensure_ascii=False)[:200]}")

    def act(self, cmd, *args, tries=4):
        for i in range(tries):
            ctx, obs = self.sync()
            d = jl(sh(cmd, "--session", self.s, "--context", ctx, "--observation", obs,
                      "--tab-id", str(self.tab), *args, "--json"))
            if d.get("code") == "permission_denied" and i + 1 < tries:
                time.sleep(0.3)
                continue
            return d
        return {}

    def js(self, expr):
        return self.act("evaluate", expr).get("value")

    def ev(self, expr, tries=6):
        for _ in range(tries):
            v = self.act("evaluate", expr).get("value")
            if v not in (None, ""):
                return v
            time.sleep(0.2)
        return None

    def evj(self, expr):
        raw = self.ev(f"JSON.stringify({expr})")
        return json.loads(raw) if raw else None

    def wait_ready(self, tries=80):
        for _ in range(tries):
            v = self.ev("JSON.stringify([document.readyState, window.__qaSnapshot && __qaSnapshot().statusReady])")
            if v:
                ready, sready = json.loads(v)
                if ready == "complete" and sready:
                    time.sleep(0.25)
                    return True
            time.sleep(0.25)
        return False

    def open(self, url, tag):
        d = jl(sh("navigate", "--session", self.s, "--tab-id", str(self.tab), f"{url}&case={tag}", "--json", timeout=60))
        if d.get("reached") != "load":
            self.act("navigate", f"{url}&case={tag}")
        if not self.wait_ready():
            raise RuntimeError(f"页面未就绪: {tag}")

    def shot(self, name):
        self.act("screenshot", "--out", str(SHOTS / f"{name}.png"))

    def facts(self) -> dict:
        return self.evj("__qaFacts()") or {}

    def snap(self) -> dict:
        return self.evj("__qaSnapshot()") or {}

    def fill_prompt(self, text):
        self.js("(() => { const p = document.querySelector('#prompt'); p.value = %s; "
                "p.dispatchEvent(new Event('input', {bubbles:true})); return 1; })()" % json.dumps(text, ensure_ascii=False))

    def submit(self):
        # 单发：evaluate 无返回值会被 bsk 重试，点一次变多次（上游会多扣）
        self.js("(() => { const p = document.querySelector('#prompt'); "
                "p.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true})); return 1; })()")

    def wait_running(self, tries=40):
        for _ in range(tries):
            s = self.snap()
            if s.get("activeStatus") == "running":
                return True
            time.sleep(0.2)
        return False

    def wait_done(self, tries=90):
        for _ in range(tries):
            s = self.snap()
            vids = self.facts().get("videos") or []
            if s.get("activeStatus") in ("done", "error") and (vids or s.get("activeStatus") == "error"):
                return s.get("activeStatus")
            time.sleep(0.5)
        return None

    def add_ref(self):
        # 32×32 的 PNG：够走完 File → canvas → data URI 的真实压缩链路
        png = _png_32()
        b64 = base64.b64encode(png).decode()
        self.js("""(async () => {
          const b = atob(%s); const u = new Uint8Array(b.length);
          for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
          const dt = new DataTransfer();
          dt.items.add(new File([u], 'frame.png', {type: 'image/png'}));
          const input = document.querySelector('#fileInput');
          input.files = dt.files;
          input.dispatchEvent(new Event('change', {bubbles: true}));
          return 1;
        })()""" % json.dumps(b64))
        for _ in range(60):
            v = self.ev("JSON.stringify(Array.from(document.querySelectorAll('#refTray .ref-chip img')).map(i => (i.src||'').slice(0,11)))")
            if v and "data:image/" in v:
                return True
            time.sleep(0.2)
        return False


def _png_32() -> bytes:
    """生成一张真的 32×32 PNG（不依赖 PIL 时用最小 zlib 手写太脆，直接跑 python 的 zlib）。"""
    import struct
    import zlib
    w = h = 32
    raw = b"".join(b"\x00" + bytes([40, 60, 90] * w) for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def main() -> int:
    if not port_open(MOCK_PORT):
        p = subprocess.Popen([sys.executable, "tools/mock_avm.py"], cwd=str(ROOT),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PROCS.append(p)
        for _ in range(60):
            if port_open(MOCK_PORT):
                break
            time.sleep(0.2)
    main_base = f"http://127.0.0.1:{MAIN_PORT}"
    noavm_base = f"http://127.0.0.1:{NOAVM_PORT}"
    spawn(MAIN_PORT, {"AVM_COOKIE": "mock-session"})
    spawn(NOAVM_PORT, {"AVM_COOKIE": "", "AVM_COOKIE_FILE": "/tmp/definitely-no-avm-cookie.txt"})

    st = http_json(main_base + "/api/status")
    v = st["video"]
    print(f"[env] video={json.dumps(v, ensure_ascii=False)[:160]}", flush=True)

    gen_dir = ROOT / "assets" / "generated"
    before_files = {p.name for p in gen_dir.glob("vid-*.mp4")}

    b = B()
    # ---------- G1 口径 ----------
    b.open(main_base + "/?theme=dark&mode=video", "G1")
    f = b.facts()
    chk("G1 徽标自称真实出片", "真实出片 · minimaxH3" in (f.get("apiBadge") or ""), True)
    chk("G1 提示写真实出片且标明文生视频", "纯文生视频" in (f.get("hint") or ""), True)
    chk("G1 积分显示免费", b.ev("document.querySelector('#costValue').textContent"), "免费")
    s = b.snap()
    chk("G1 快照判定真实视频", (s.get("realVideo"), s.get("vidConfigured"), s.get("vidAllowPaid")), (True, True, False))

    # ---------- G2 档位 ----------
    bar = b.evj("[...['pSize','pDuration','pCount']].map(id => (document.getElementById(id)?.textContent||'').replace(/\\s+/g,' ').trim())")
    chk("G2 参数条只有 720P/8s/1个", bar, ["1:1 720P", "8s", "1个"])
    b.js("document.querySelector('#pCount').click()")
    time.sleep(0.4)
    chk("G2 数量不可调（无浮层）", b.ev("!!document.querySelector('.popover')"), False)
    chk("G2 数量 toast 说明原因", "一次只产 1 个视频" in (b.ev("document.querySelector('#toasts')?.textContent") or ""), True)
    b.js("document.querySelector('#pSize').click()")
    time.sleep(0.4)
    chk("G2 1080P 按钮被禁用", b.ev("document.querySelector('[data-vtier=\"paid\"]')?.disabled"), True)
    b.js("(() => { const x = document.querySelector('[data-vtier=\"paid\"]'); x.disabled = false; x.click(); return 1; })()")
    time.sleep(0.4)
    chk("G2 强点计费档也不生效", b.snap().get("videoTier"), "free")
    chk("G2 强点计费档有拒绝提示", "AVM_ALLOW_PAID" in (b.ev("document.querySelector('#toasts')?.textContent") or ""), True)
    b.shot("01-params")
    b.js("document.body.click()")

    # ---------- G3 文生视频 ----------
    n0 = len(records())
    up = QA / "mock-upstream.jsonl"
    img0 = len(up.read_text(encoding="utf-8").splitlines()) if up.exists() else 0
    marker = f"C8-{int(time.time())}"
    b.fill_prompt(f"{marker} 雨夜霓虹街头，镜头缓慢推近，电影感")
    b.submit()
    ok_run = b.wait_running()
    chk("G6 提交后进入运行态且可继续操作", (ok_run, b.snap().get("busy")), (True, False))
    chk("G6 运行卡有计时与任务号", b.ev("!!document.getElementById('convVidClock')"), True)
    time.sleep(2.8)          # 等 toast 散掉再截，否则会压住参数条
    b.shot("02-running")
    status = b.wait_done()
    chk("G3 文生视频上游只收 1 次", len(rec_since(n0, marker)), 1)
    rec = (rec_since(n0, marker) or [{}])[0]
    chk("G3 请求体无首帧", (rec.get("role"), rec.get("frame_bytes")), (None, 0))
    chk("G3 模型/分辨率/时长", (rec.get("model"), rec.get("resolution"), rec.get("duration")), ("minimaxH3", "720p", 8))
    chk("G3 无首帧用页面比例", rec.get("ratio"), "1:1")
    chk("G10 轮询成功", status, "done")
    img1 = len(up.read_text(encoding="utf-8").splitlines()) if up.exists() else 0
    chk("G3 视频不误走图片上游", img1 - img0, 0)
    fa = b.facts()
    chk("G7 出片是 video 元素", len(fa.get("videos") or []), 1)
    vid = (fa.get("videos") or [{}])[0]
    chk("G7 成片是本地 mp4", bool(re.search(r"/assets/generated/vid-.+\.mp4$", vid.get("src") or "")), True)
    chk("G7 播放器可解码", (vid.get("rs") or 0) >= 1 and vid.get("err") is None, True)
    chk("G7 来源标注 minimaxH3", "REAL · minimaxH3" in " ".join(fa.get("sourceTags") or []), True)
    tags = " ".join(fa.get("bubbleTags") or [])
    chk("G3 标签写文生视频", "文生视频" in tags, True)
    chk("G3 标签是 720P/8s/1个", ("720P" in tags and "8s" in tags and "1 个" in tags), True)
    chk("G7 会话头写 1 个结果", "1 个结果" in (fa.get("convDesc") or ""), True)
    b.shot("03-done")
    b.js("document.querySelector('[data-nav=\"资产\"]')?.click()")
    time.sleep(0.6)
    chk("G7 写入资产库", (b.facts().get("assetVideos") or 0) >= 1, True)
    b.shot("03b-assets")
    b.js("document.getElementById('drawerClose')?.click()")
    time.sleep(0.3)
    after_files = {p.name for p in gen_dir.glob("vid-*.mp4")}
    chk("G7 成片确实落盘", len(after_files - before_files) >= 1, True)

    # ---------- G4 图生视频 ----------
    b.open(main_base + "/?theme=dark&mode=video", "G4")
    ok_ref = b.add_ref()
    chk("G4 参考图压缩成 data URI", ok_ref, True)
    chk("G4 首图标记为首帧", b.ev("document.querySelector('#refTray .ref-index')?.textContent"), "首帧")
    chk("G4 提示切到有首帧口径", "第 1 张参考作首帧" in (b.facts().get("hint") or ""), True)
    n1 = len(records())
    marker2 = f"C16-{int(time.time())}"
    b.fill_prompt(f"{marker2} 让画面里的物体缓慢旋转")
    b.submit()
    b.wait_running()
    st2 = b.wait_done()
    chk("G4 提交成功", st2, "done")
    rec2 = (rec_since(n1, marker2) or [{}])[0]
    chk("G4 走 first_frame 通道", rec2.get("role"), "first_frame")
    chk("G4 首帧被裁成 9:16", bool(rec2.get("frame_size")) and abs(rec2["frame_size"][0] / rec2["frame_size"][1] - 9 / 16) < 0.02, True)
    chk("G4 比例跟随首帧", rec2.get("ratio"), "adaptive")
    chk("G4 标签写图生视频", "图生视频" in " ".join(b.facts().get("bubbleTags") or []), True)
    b.shot("04-image-to-video")

    # ---------- G8 失败 ----------
    b.open(main_base + "/?theme=dark&mode=video", "G8")
    marker3 = f"C18-{int(time.time())}"
    b.fill_prompt(f"__MOCK_FAIL__ {marker3} 故意失败")
    b.submit()
    b.wait_running()
    st3 = b.wait_done()
    f3 = b.facts()
    chk("G8 失败态收敛", st3, "error")
    chk("G8 透传上游原因", "mock upstream failed" in " ".join(f3.get("alerts") or []), True)
    chk("G8 不出现假成片", len(f3.get("videos") or []), 0)
    chk("G8 提供重试", b.ev("!!document.getElementById('convRetry')"), True)
    b.shot("05-failed")

    # ---------- G6 停止等待 ----------
    b.open(main_base + "/?theme=dark&mode=video", "G6b")
    b.fill_prompt(f"C20-{int(time.time())} 停止等待探针")
    b.submit()
    b.wait_running()
    b.js("document.getElementById('convStopWait')?.click()")
    time.sleep(0.6)
    chk("G6 停止等待后标 error", b.snap().get("activeStatus"), "error")
    chk("G6 说明只停轮询不取消上游", "上游任务仍在跑" in " ".join(b.facts().get("alerts") or []), True)

    # ---------- 响应式 ----------
    b.open(main_base + "/?theme=dark&mode=video", "R1")
    b.act("emulate", "--width", "390", "--height", "844", "--dpr", "2", "--mobile")
    time.sleep(0.6)
    d = jl(sh("navigate", "--session", b.s, "--tab-id", str(b.tab),
              f"{main_base}/?theme=dark&mode=video&case=R1b", "--json", timeout=60))
    if d.get("reached") != "load":
        b.act("navigate", f"{main_base}/?theme=dark&mode=video&case=R1b")
    b.wait_ready()
    b.js("Array.from(document.querySelectorAll('.session-item[data-id]'))"
         ".find((el) => el.textContent.includes(\"C8-\"))?.click()")
    time.sleep(0.8)
    fr = b.facts()
    chk("R 390 无横向溢出", fr.get("overflow"), False)
    chk("R 播放器在窄屏不超宽", b.ev("(() => { const v = document.querySelector('.result-card.is-video-file'); return v ? Math.round(v.getBoundingClientRect().width) <= 380 : 'none'; })()"), True)
    b.shot("06-390")

    b.act("emulate", "--width", "1500", "--height", "1000", "--dpr", "1")
    time.sleep(0.4)

    # ---------- G9 刷新 ----------
    b.open(main_base + "/?theme=dark&mode=video", "G9")
    d = jl(sh("navigate", "--session", b.s, "--tab-id", str(b.tab),
              f"{main_base}/?theme=dark&mode=video&case=G9r", "--json", timeout=60))
    if d.get("reached") != "load":
        b.act("navigate", f"{main_base}/?theme=dark&mode=video&case=G9r")
    b.wait_ready()
    b.js("Array.from(document.querySelectorAll('.session-item[data-id]'))"
         ".find((el) => el.textContent.includes(\"C8-\"))?.click()")
    time.sleep(0.8)
    chk("G9 刷新后成片仍在", len(b.facts().get("videos") or []), 1)

    b.js("""(() => {
      const rec = {v: 1, at: Date.now(), refsDropped: false, sessions: [{
        id: 'sStale', title: '中断探针', mode: 'video', prompt: '中断探针', params: ['视频生成'],
        images: [], source: 'real', error: null, model: 'minimaxH3', at: Date.now(),
        status: 'running', vidId: 'cgt-mock-999', vidStartedAt: Date.now()}]};
      localStorage.setItem('jimeng-sessions-v1', JSON.stringify(rec));
      return 1;
    })()""")
    d = jl(sh("navigate", "--session", b.s, "--tab-id", str(b.tab),
              f"{main_base}/?theme=dark&mode=video&case=G9s", "--json", timeout=60))
    if d.get("reached") != "load":
        b.act("navigate", f"{main_base}/?theme=dark&mode=video&case=G9s")
    b.wait_ready()
    b.js("document.querySelector('.session-item[data-id]')?.click()")
    time.sleep(0.8)
    f9 = b.facts()
    chk("G9 running 会话刷新标中断", b.snap().get("activeStatus"), "error")
    chk("G9 中断原因说人话", "轮询中断" in " ".join(f9.get("alerts") or []), True)
    chk("G9 中断后不再转圈", f9.get("running"), False)

    # ---------- G10 未配置 ----------
    b.open(noavm_base + "/?theme=dark&mode=video", "G10")
    fq = b.facts()
    chk("G10 未配置时不冒充真实", b.snap().get("realVideo"), False)
    chk("G10 徽标写视频演示", "视频演示" in (fq.get("apiBadge") or ""), True)
    chk("G10 提示写明缺 cookie", "AVM_COOKIE" in (fq.get("hint") or ""), True)
    n2 = len(records())
    b.fill_prompt(f"C24-{int(time.time())} 未配置实例不应发请求")
    b.submit()
    time.sleep(3.0)
    chk("G10 演示模式零上游请求", len(records()) - n2, 0)
    chk("G10 演示结果标注 DEMO", "DEMO" in " ".join(b.facts().get("sourceTags") or []), True)


    total = len(passes) + len(fails)
    print(f"\n{len(passes)}/{total} passed", flush=True)
    REPORT.write_text(json.dumps({
        "ok": not fails, "pass": len(passes), "total": total, "failed": fails,
        "at": time.strftime("%F %T"), "base": main_base,
        "video": v,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if not fails else 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        for p in PROCS:
            p.terminate()
        if B_SESSION:
            subprocess.run(["bsk", "session", "stop", "--session", B_SESSION, "--json"], capture_output=True)
    sys.exit(code)
