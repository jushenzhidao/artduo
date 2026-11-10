#!/usr/bin/env python3
"""基于 Chrome DevTools Protocol 的确定性界面自检。

解决两个 headless CLI 的失真：
  1. macOS Chrome 的 --window-size 有 500px 最小宽度，截图被裁切；
     CDP Emulation.setDeviceMetricsOverride 可精确设定 390 / 1024 / 1440 CSS 视口。
  2. CLI --virtual-time-budget 会让合成层出现伪影；CDP 在真实时间轴上截图。

用法：
  python3 tools/qa_cdp.py --shots       # 多尺寸 / 主题 / 状态截图
  python3 tools/qa_cdp.py --interact    # 交互链路验证（不触发真实生成）
  python3 tools/qa_cdp.py --real-test   # 触发一次真实 flux-3-image 生成
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "qa" / "shots" / "cdp"
REPORT = ROOT / "qa" / "qa-report.json"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333
BASE = "http://127.0.0.1:8788/"


class CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=180, suppress_origin=True)
        self.n = 0

    def cmd(self, method: str, params: dict | None = None, timeout: int = 180):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.ws.settimeout(max(1, min(30, deadline - time.monotonic())))
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def eval(self, expr: str, await_promise: bool = False):
        r = self.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                          "awaitPromise": await_promise})
        if "exceptionDetails" in r:
            raise RuntimeError(f"JS exception: {r['exceptionDetails']}")
        return r.get("result", {}).get("value")

    def navigate(self, url: str, width: int, height: int):
        expected_theme = "light" if "theme=light" in url else "dark"
        if "theme=" not in url:
            url = url + ("&" if "?" in url else "?") + "theme=" + expected_theme
        self.cmd("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1,
            "mobile": width < 700, "screenWidth": width, "screenHeight": height,
        })
        if "theme=" not in url:
            try:
                self.eval("localStorage.removeItem('jimeng-theme')")
            except Exception:
                pass
        self.cmd("Page.navigate", {"url": url})
        check = ("location.href === " + json.dumps(url) +
                 " && document.readyState === 'complete' && !!document.querySelector('.insp-card')" +
                 " && document.documentElement.dataset.theme === '" + expected_theme + "'" +
                 " && !!document.querySelector('#apiBadge')")
        deadline = time.monotonic() + 25
        ready = False
        while time.monotonic() < deadline:
            try:
                if self.eval(check):
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.2)
        if not ready:
            raise RuntimeError(f"page not ready / theme mismatch: {url}")
        time.sleep(0.55)  # 等 CSS 入场动画结束

    def shot(self, path: Path):
        r = self.cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(r["data"]))
        return path.stat().st_size

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def start_chrome():
    proc = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions", "--no-proxy-server",
        "--hide-scrollbars", "--remote-allow-origins=*",
        f"--remote-debugging-port={PORT}",
        "--user-data-dir=/tmp/jimeng-qa/cdp-profile",
        "--window-size=1440,900", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as r:
                json.loads(r.read())
            break
        except Exception:
            time.sleep(0.25)
    else:
        proc.terminate()
        raise RuntimeError("Chrome CDP did not start")
    # 新开一个干净的 page target
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=5) as r:
        target = json.loads(r.read())
    return proc, target["webSocketDebuggerUrl"]


def metrics(cdp: CDP):
    expr = """(() => {
      const r = s => { const e=document.querySelector(s); if(!e) return null; const b=e.getBoundingClientRect();
        return [Math.round(b.x), Math.round(b.width), Math.round(b.right)]; };
      return { vw: innerWidth, docW: document.documentElement.scrollWidth,
        modeSwitch: r('#modeSwitch'), insp: r('#inspiration'), composer: r('#composer'),
        bar: r('.composer-toolbar'), left: r('#toolbarLeft'), right: r('.toolbar-right'),
        send: r('#sendBtn'), lightbox: getComputedStyle(document.querySelector('#lightbox')).display,
        welcome: getComputedStyle(document.querySelector('#welcome')).display,
        apiBadge: document.querySelector('#apiBadge')?.textContent.trim(),
        cards: document.querySelectorAll('.insp-card').length };
    })()"""
    return cdp.eval(expr)


SHOT_PLAN = [
    ("desktop-1440-dark", 1440, 900, ""),
    ("desktop-1440-light", 1440, 900, "?theme=light"),
    ("tablet-1024-dark", 1024, 768, ""),
    ("mobile-390-dark", 390, 844, ""),
    ("mobile-390-light", 390, 844, "?theme=light"),
    ("video-1440", 1440, 900, "?mode=video"),
    ("result-1440", 1440, 900, "?qa=result"),
    ("popover-1440", 1440, 900, "?qa=popover"),
    ("drawer-1440", 1440, 900, "?qa=drawer"),
    ("error-1440", 1440, 900, "?qa=error"),
    ("lowcredit-mobile", 390, 844, "?qa=lowcredit"),
    ("realresult-1440", 1440, 900, "?qa=realresult"),
]


def run_shots(cdp: CDP, report: dict):
    for name, w, h, query in SHOT_PLAN:
        cdp.navigate(BASE + query, w, h)
        size = cdp.shot(SHOTS / f"{name}.png")
        m = metrics(cdp)
        overflow = m["docW"] > m["vw"] + 1
        report["shots"].append({"name": name, "viewport": [w, h], "bytes": size,
                                "metrics": m, "overflow": overflow})
        print(f"  {name:22s} vw={m['vw']:4d} docW={m['docW']:4d} "
              f"sendRight={m['send'][2] if m['send'] else '-':>5} overflow={overflow} "
              f"lightbox={m['lightbox']} {size // 1024}K")


def run_interact(cdp: CDP, report: dict):
    cdp.navigate(BASE + "?mode=image", 1440, 900)
    checks = []

    def chk(name, got, want):
        ok = got == want
        checks.append({"name": name, "ok": ok, "got": got, "want": want})

    chk("启动为图片生成", cdp.eval("document.querySelector('#modeChip').textContent"), "图片生成")
    chk("生成按钮初始禁用", cdp.eval("document.querySelector('#sendBtn').disabled"), True)
    chk("三张灵感卡", cdp.eval("document.querySelectorAll('.insp-card').length"), 3)

    # 点「试一试」→ 提示词回填 → 按钮启用
    cdp.eval("document.querySelector('.insp-card').click()")
    chk("示例回填后按钮启用", cdp.eval("document.querySelector('#sendBtn').disabled"), False)
    chk("提示词非空", cdp.eval("document.querySelector('#prompt').value.length > 10"), True)

    # 切视频模式
    cdp.eval("document.querySelector('[data-mode=video]').click()")
    chk("切换到视频生成", cdp.eval("document.querySelector('#modeChip').textContent"), "视频生成")
    chk("视频模式有时长按钮", cdp.eval("!!document.querySelector('#pDuration')"), True)
    chk("视频默认 1 个", cdp.eval("document.querySelector('#pCount').textContent.includes('1个')"), True)

    # 参数浮层
    cdp.eval("document.querySelector('#pSize').click()")
    chk("比例浮层打开", cdp.eval("document.querySelectorAll('.ratio-opt').length"), 12)
    cdp.eval("document.querySelector('.ratio-opt[data-r=\"16:9\"]').click()")
    chk("比例选中后浮层重开", cdp.eval("document.querySelectorAll('.ratio-opt').length"), 12)
    chk("比例已更新", cdp.eval("document.querySelector('#pSize').textContent.includes('16:9')"), True)
    cdp.eval("document.querySelector('.pop-scrim').click()")
    chk("浮层可关闭", cdp.eval("document.querySelectorAll('.popover').length"), 0)

    # 创作类型下拉
    cdp.eval("document.querySelector('#pType').click()")
    chk("创作类型 7 项", cdp.eval("document.querySelectorAll('.pop-opt[data-id]').length"), 7)
    cdp.eval("document.querySelector('.pop-scrim').click()")

    # 演示生成（不触发真实请求）
    cdp.eval("""(() => { const s=document.querySelector('#pSettings'); s.click();
      const t=document.querySelector('#popToggleReal'); if(t) t.click(); })()""")
    chk("可切到演示模式", cdp.eval("document.querySelector('#apiBadge').textContent.includes('演示')"), True)
    cdp.eval("""(() => { const p=document.querySelector('#prompt');
      p.value='一只在森林中奔跑的小鹿，晨雾，电影感'; p.dispatchEvent(new Event('input',{bubbles:true}));
      document.querySelector('#sendBtn').click(); })()""")
    time.sleep(2.4)
    chk("演示生成出结果", cdp.eval("document.querySelectorAll('.result-card').length >= 1"), True)
    chk("结果标注 DEMO", cdp.eval("document.querySelector('.source-tag')?.textContent.includes('DEMO')"), True)
    cdp.shot(SHOTS / "interact-demo-result.png")

    # 脏态：积分不足
    cdp.eval("""(() => { document.querySelector('#pSettings').click();
      document.querySelector('#popForceLow').click();
      const p=document.querySelector('#prompt'); p.value='赛博城市夜景';
      p.dispatchEvent(new Event('input',{bubbles:true}));
      document.querySelector('#sendBtn').click(); })()""")
    time.sleep(0.4)
    chk("积分不足被拦截", cdp.eval("document.querySelector('#hintText').textContent.includes('积分不足')"), True)
    cdp.shot(SHOTS / "interact-lowcredit.png")

    # 主题切换
    cdp.eval("document.querySelector('#themeBtn').click()")
    chk("主题切到浅色", cdp.eval("document.documentElement.dataset.theme"), "light")

    for c in checks:
        print(f"  {'PASS' if c['ok'] else 'FAIL'}  {c['name']}" +
              ("" if c["ok"] else f"  got={c['got']!r} want={c['want']!r}"))
    report["interact"] = checks
    return all(c["ok"] for c in checks)


def run_real_test(cdp: CDP, report: dict):
    """触发一次真实 flux-3-image 生成（1 张 1K）。"""
    cdp.navigate(BASE, 1440, 900)
    status = cdp.eval("document.querySelector('#apiBadge').textContent")
    if "真实" not in status:
        raise RuntimeError(f"真实模式未就绪: {status}")
    prompt = "A single red ceramic cube on a cream linen table, soft studio side light, minimalist product photography, no text"
    cdp.eval("""(() => { const p=document.querySelector('#prompt'); p.value=%s;
      p.dispatchEvent(new Event('input',{bubbles:true}));
      document.querySelector('#sendBtn').click(); })()""" % json.dumps(prompt))
    print("  已提交真实生成，等待上游…", flush=True)
    deadline = time.monotonic() + 170
    result = None
    while time.monotonic() < deadline:
        result = cdp.eval("""(() => {
          const cards=[...document.querySelectorAll('.result-card')];
          const err=document.querySelector('.alert-bar')?.textContent||'';
          const hint=document.querySelector('#hintText').textContent;
          return { busy: !document.querySelector('#sendBtn').disabled,
                   cards: cards.length, real: !!document.querySelector('.source-tag.real'),
                   src: document.querySelector('.result-card img')?.getAttribute('src')||'',
                   err: err.slice(0,200), hint: hint.slice(0,160) }; })()""")
        if result["real"] or result["err"]:
            break
        time.sleep(2)
    cdp.shot(SHOTS / "real-generate-result.png")
    report["real_test"] = {"prompt": prompt, "result": result}
    print(f"  结果: {json.dumps(result, ensure_ascii=False)}", flush=True)
    return bool(result and result["real"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", action="store_true")
    ap.add_argument("--interact", action="store_true")
    ap.add_argument("--real-test", action="store_true")
    args = ap.parse_args()
    if not (args.shots or args.interact or args.real_test):
        ap.error("选择 --shots / --interact / --real-test")

    report = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "shots": []}
    proc, ws_url = start_chrome()
    cdp = CDP(ws_url)
    ok = True
    try:
        cdp.cmd("Page.enable")
        cdp.cmd("Runtime.enable")
        if args.shots:
            print("== 精确视口截图 ==")
            run_shots(cdp, report)
        if args.interact:
            print("== 交互链路 ==")
            ok = run_interact(cdp, report) and ok
        if args.real_test:
            print("== 真实生成功能测试 ==")
            ok = run_real_test(cdp, report) and ok
    finally:
        cdp.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告: {REPORT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
