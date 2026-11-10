#!/usr/bin/env python3
"""刷新接续 + 视频进度条自检（零计费）。

覆盖：
  R1 提交图片后，localStorage 里落下 taskId 快照
  R2 刷新页面 → 队列按 taskId 自动接续（不是静默消失）
  R3 接续后能拿到结果，且成功后清掉快照
  R4 视频提交后卡片出现进度条、百分比与计时
  R5 刷新后视频会话仍是 running，并继续轮询（不再直接标「轮询中断」）

端口：8941 mock flux / 8942 mock avm / 8943 clone
注：bsk 需要浏览器空闲（被用户接管时会报 permission_denied / hand-back）；
    无头替代方案见 qa/shots/video-progress.png 对应的 puppeteer 跑法。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path("/tmp/jimeng-resume-check")
TMP.mkdir(parents=True, exist_ok=True)
SHOTS = ROOT / "qa" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)
BROWSER = "90d43dd3"

MOCK = "http://127.0.0.1:8941"
AVM = "http://127.0.0.1:8942"
BASE = "http://127.0.0.1:8943"

procs: list[subprocess.Popen] = []
checks: list[tuple[str, bool, object, object]] = []


def chk(name: str, got, want) -> None:
    ok = got == want
    checks.append((name, ok, got, want))
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  got={got!r} want={want!r}"),
          flush=True)


def sh(*args, timeout=120) -> str:
    r = subprocess.run(["bsk", *args], capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip()


def jl(raw: str) -> dict:
    try:
        return json.loads(raw, strict=False)
    except Exception:
        return {"__raw": raw[:300]}


class B:
    def __init__(self) -> None:
        d = jl(sh("session", "start", "--json", "--browser", BROWSER,
                  "--width", "1440", "--height", "960", "--no-focus", "--name", "resume-check"))
        self.s = d.get("session_id")
        if not self.s:
            raise RuntimeError(f"session start 失败: {d}")
        self.tab = jl(sh("tab", "create", "--session", self.s, "--url", "about:blank",
                         "--json")).get("tab_id")

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

    def ev(self, expr, tries=8):
        for _ in range(tries):
            v = self.act("evaluate", expr).get("value")
            if v not in (None, ""):
                return v
            time.sleep(0.2)
        return None

    def evj(self, expr):
        raw = self.ev(f"JSON.stringify({expr})")
        return json.loads(raw) if raw else None

    def open(self, url):
        d = jl(sh("navigate", "--session", self.s, "--tab-id", str(self.tab), url, "--json",
                  timeout=60))
        if d.get("reached") != "load":
            self.act("navigate", url)
        for _ in range(60):
            if self.ev("window.__qaSnapshot && window.__qaSnapshot().statusReady"):
                return True
            time.sleep(0.3)
        return False

    def stop(self):
        subprocess.run(["bsk", "session", "stop", "--session", self.s, "--json"],
                       capture_output=True)


def spawn(args: list[str], env: dict, log: str) -> subprocess.Popen:
    f = open(TMP / log, "w")
    e = {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
         "HOME": str(Path.home())}
    e.update(env)
    p = subprocess.Popen(args, cwd=str(ROOT), env=e, stdout=f, stderr=subprocess.STDOUT)
    procs.append(p)
    return p


def wait_http(url: str, timeout: float = 25) -> bool:
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    end = time.time() + timeout
    while time.time() < end:
        try:
            with op.open(url, timeout=2) as r:
                r.read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


def wait_for(fn, timeout: float, cond=None):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
        except Exception:
            last = None
        if cond is None:
            if last:
                return last
        elif cond(last):
            return last
        time.sleep(0.4)
    return last


def main() -> int:
    spawn([sys.executable, "tools/mock_upstream.py"],
          {"MOCK_PORT": "8941", "MOCK_DELAY_MS": "8000", "MOCK_BILLING": "0"}, "mock-flux.log")
    spawn([sys.executable, "tools/mock_avm.py"],
          {"MOCK_AVM_PORT": "8942", "MOCK_AVM_QUEUED": "8"}, "mock-avm.log")
    assert wait_http(f"{MOCK}/__stats"), "mock flux 未就绪"
    assert wait_http(f"{AVM}/v/test.mp4"), "mock avm 未就绪"
    spawn([sys.executable, "server.py"], {
        "PORT": "8943", "JIMENG_HOST": "127.0.0.1",
        "BFL_BASE": MOCK, "BFL2OPENAI_API_KEY": "sk-mock-resume", "JIMENG_REAL": "1",
        "JIMENG_BILLING_MODE": "local", "JIMENG_START_CREDITS": "20000",
        "JIMENG_BILLING_DB": str(TMP / "billing.db"),
        "JIMENG_CONFIG_FILE": str(TMP / "runtime.json"),
        "JIMENG_WORKERS": "2", "JIMENG_MAX_CALLS": "50",
        "AVM_BASE": AVM, "AVM_COOKIE": "resume-cookie", "AVM_MODEL": "minimaxH3",
    }, "server.log")
    assert wait_http(f"{BASE}/api/health"), "clone 未就绪"

    b = B()
    try:
        assert b.open(f"{BASE}/?theme=dark"), "页面未就绪"
        chk("R0 视频链路可用", b.evj("window.__qaSnapshot()").get("vidConfigured"), True)

        # ---------- 图片：提交 → 刷新 → 接续 ----------
        b.act("fill", "--selector", "#prompt", "--value", "刷新接续测试图")
        b.act("click", "--selector", "#sendBtn")
        raw = wait_for(lambda: b.ev("localStorage.getItem('jimeng-jobs-v1')"), 15)
        chk("R1 落盘队列快照", bool(raw) and "taskId" in (raw or ""), True)
        snap = b.evj("window.__qaSnapshot()")
        chk("R1 有 1 个在飞任务", snap.get("jobs"), 1)

        assert b.open(f"{BASE}/?theme=dark"), "刷新后页面未就绪"
        snap = b.evj("window.__qaSnapshot()")
        chk("R2 刷新后任务仍在跑", snap.get("jobs"), 1)
        chk("R2 自动切到队列视图", snap.get("queueView"), True)

        facts = wait_for(lambda: b.evj("window.__qaFacts()"), 40,
                         cond=lambda f: (f or {}).get("resultCards", 0) > 0)
        chk("R3 接续后拿到结果", (facts or {}).get("resultCards"), 1)
        chk("R3 没有报错条", (facts or {}).get("alerts"), [])
        chk("R3 成功后清掉快照", b.ev("localStorage.getItem('jimeng-jobs-v1')"), None)

        # ---------- 视频：进度条 + 刷新接续 ----------
        # 模式切换按钮只在欢迎页可见：先回首屏（上次生成把视图切到会话了）
        assert b.open(f"{BASE}/?theme=dark"), "首屏未就绪"
        chk("R4 模式切换可见", b.ev(
            "(() => { const el = [...document.querySelectorAll('.mode-tab[data-mode=\"video\"]')]"
            ".find((e) => e.offsetParent); if (!el) return false; el.click(); return true; })()"), True)
        b.ev("(() => { const el = document.getElementById('prompt'); el.value = '视频进度条测试';"
             " el.dispatchEvent(new Event('input', { bubbles: true })); return 1; })()")
        chk("R4 发送键可用", b.ev("!document.getElementById('sendBtn').disabled"), True)
        b.ev("document.getElementById('sendBtn').click(), 1")
        chk("R4 出现进度条", wait_for(lambda: b.ev("!!document.getElementById('convVidBar')"), 25), True)
        time.sleep(3.2)
        w = b.ev("parseFloat(document.getElementById('convVidBar').style.width) || 0")
        pct = b.ev("document.getElementById('convVidPct').textContent")
        chk("R4 进度条在推进", (w or 0) > 0, True)
        chk("R4 有百分比", bool(pct and pct.endswith("%")), True)
        chk("R4 计时在走", bool(b.ev("document.getElementById('convVidClock').textContent")), True)
        b.act("screenshot", "--out", str(SHOTS / "video-progress.png"))

        assert b.open(f"{BASE}/?theme=dark"), "视频刷新后页面未就绪"
        snap = b.evj("window.__qaSnapshot()")
        chk("R5 刷新后视频仍在跑", snap.get("activeStatus"), "running")
        chk("R5 进度条仍在", b.ev("!!document.getElementById('convVidBar')"), True)
        chk("R5 未标成轮询中断",
            "轮询中断" in (b.ev("document.body.innerText") or ""), False)

    finally:
        b.stop()

    ok = sum(1 for c in checks if c[1])
    print(f"\n{ok}/{len(checks)} passed", flush=True)
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        for p in reversed(procs):
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                p.kill()
