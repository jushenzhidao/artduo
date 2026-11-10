#!/usr/bin/env python3
"""本轮优化的端到端验证（bsk 驱动，指向 mock 实例，零计费）。

覆盖 6 件事：
  T1 提交快照：生成中切模式，会话记录仍保持提交时的参数
  T2 生成中锁定：模式/参数按钮不可点，结束后恢复
  T3 历史持久化：刷新后会话仍在；localStorage 损坏时不崩、回落示例会话
  T4 积分口径：credits=0 合法、缺失才兜底 38
  T5 失效图片：404 图片显示占位而不是空白
  T6 390 移动端：生成态渐变卡不横向溢出
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

BASE = "http://127.0.0.1:8791"
BROWSER = "90d43dd3"
QA = Path(__file__).resolve().parent.parent / "qa"
SHOTS = QA / "shots" / "optimize"
SHOTS.mkdir(parents=True, exist_ok=True)


def sh(*args, timeout=90):
    r = subprocess.run(["bsk", *args], capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip()


def jl(raw):
    try:
        return json.loads(raw, strict=False)
    except Exception:
        return {"__raw": raw[:300]}


class B:
    def __init__(self):
        d = jl(sh("session", "start", "--json", "--browser", BROWSER,
                 "--width", "1500", "--height", "1000", "--no-focus", "--name", "opt-check"))
        self.s = d.get("session_id")
        if not self.s:
            raise RuntimeError(f"session start 失败: {d}")
        self.tab = None

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
            extra = ["--tab-id", str(self.tab)] if self.tab else []
            d = jl(sh(cmd, "--session", self.s, "--context", ctx, "--observation", obs,
                      *extra, *args, "--json"))
            msg = str(d.get("message", ""))
            if d.get("code") == "permission_denied" and "observation" in msg and i + 1 < tries:
                time.sleep(0.25)
                continue
            return d
        return {}

    def ev(self, expr):
        # 页面重绘会让刚同步的观察过期，求值偶发返回空：重试到拿到值为止
        for _ in range(5):
            d = self.act("evaluate", expr)
            v = d.get("value")
            if v is not None and v != "":
                return v
            time.sleep(0.25)
        return None

    def open(self, tag):
        url = f"{BASE}/?theme=dark&case={tag}"
        d = jl(sh("navigate", "--session", self.s, "--tab-id", str(self.tab), url, "--json", timeout=60))
        if d.get("reached") != "load":
            self.act("navigate", url, tries=3)
        for _ in range(40):
            v = self.ev("JSON.stringify([typeof window.__qaFacts, document.readyState,"
                        " window.__qaSnapshot ? window.__qaSnapshot().statusReady : null])")
            if v and json.loads(v)[:2] == ["function", "complete"] and json.loads(v)[2] is True:
                time.sleep(0.45)
                return True
            time.sleep(0.25)
        raise RuntimeError(f"页面未就绪: {tag}")

    def shot(self, name):
        self.act("screenshot", "--out", str(SHOTS / f"{name}.png"))


def main() -> int:
    b = B()
    results = []
    steps = []

    def chk(name, got, want):
        ok = got == want
        results.append({"name": name, "got": got, "want": want, "ok": ok})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {got!r} (期望 {want!r})")
        return ok

    try:
        b.tab = jl(sh("tab", "create", "--session", b.s, "--url",
                      f"{BASE}/?theme=dark&case=opt0", "--json")).get("tab_id")
        if not b.tab:
            print("创建标签页失败", file=sys.stderr)
            return 2
        # 清掉上一轮遗留的本地历史，保证 T1/T3 的起点确定
        b.ev("localStorage.removeItem('jimeng-sessions-v1'), 1")
        b.open("opt1")

        # ---------- T1 + T2 ----------
        b.act("fill", "--selector", "#prompt", "--value", "__MOCK_SLOW__ 提交快照验证")
        b.act("press", "Enter", "--selector", "#prompt")
        time.sleep(0.45)
        busy = b.ev("document.querySelector('.app').classList.contains('is-busy')")
        locked = b.ev("getComputedStyle(document.querySelector('.mode-tab')).pointerEvents")
        paramLocked = b.ev("getComputedStyle(document.querySelector('.param-btn')).pointerEvents")
        chk("T2 生成中 app.is-busy", busy, True)
        chk("T2 模式按钮不可点", locked, "none")
        chk("T2 参数按钮不可点", paramLocked, "none")
        b.shot("01-generating-desktop")

        # 生成中强行切到视频模式（DOM click 绕过 pointer-events，制造最坏竞态）
        b.ev("document.querySelector('.mode-tab[data-mode=\"video\"]').click(), 1")
        time.sleep(0.15)
        chk("T1 切换确实生效（state.mode）", b.ev("window.__qaSnapshot().mode"), "video")

        for _ in range(60):
            if b.ev("document.querySelectorAll('.result-card, .alert-bar').length > 0"):
                break
            time.sleep(0.2)
        time.sleep(0.4)

        tags = b.ev("JSON.stringify([...document.querySelectorAll('.bubble-tag')].map(x=>x.textContent.trim()))")
        tags = json.loads(tags or "[]")
        chk("T1 会话仍记为图片生成", "图片生成" in tags, True)
        chk("T1 参数仍是 1024×1024 · 1K", "1024×1024 · 1K" in tags, True)
        chk("T1 张数仍是 1 张", "1 张" in tags, True)
        chk("T2 完成后解锁", b.ev("document.querySelector('.app').classList.contains('is-busy')"), False)
        chk("T2 完成后模式按钮可点",
            b.ev("getComputedStyle(document.querySelector('.mode-tab')).pointerEvents"), "auto")
        b.shot("02-snapshot-result")

        # ---------- T5 失效图片占位 ----------
        b.ev("""(() => {
          const box = document.createElement('div');
          box.className = 'result-card';
          box.innerHTML = '<img src="/assets/__not_exist__.png" alt="">';
          document.querySelector('#conversation').appendChild(box);
          return 1;
        })()""")
        time.sleep(0.9)
        chk("T5 404 图片被标记", b.ev("!!document.querySelector('.result-card.is-broken')"), True)

        # ---------- T3 持久化 ----------
        title = b.ev("document.querySelector('.session-item[data-id] .label').textContent")
        store = b.ev("(localStorage.getItem('jimeng-sessions-v1') || '').length")
        chk("T3 已写入 localStorage", store > 50, True)
        b.shot("03-before-reload")

        b.open("opt2")   # 换 URL 触发真实重载
        chk("T3 刷新后会话仍在", b.ev("document.querySelectorAll('.session-item[data-id]').length") >= 1, True)
        # 会话是异步写入的：只要求「那条记录还在」，不要求它保持在第一位
        chk("T3 原会话仍在", b.ev(
            "[...document.querySelectorAll('.session-item[data-id] .label')]"
            f".some(e => e.textContent === {json.dumps(title)})"), True)
        b.shot("04-after-reload")

        # 损坏数据：必须能降级，不能白屏
        b.ev("localStorage.setItem('jimeng-sessions-v1', '{oops not json'), 1")
        b.open("opt3")
        chk("T3 坏数据不崩（示例会话回退）",
            b.ev("document.querySelectorAll('.session-item[data-id]').length"), 3)
        chk("T3 坏数据仍渲染灵感卡", b.ev("document.querySelectorAll('.insp-card').length"), 3)

        # ---------- T4 积分口径 ----------
        chk("T4 credits=0 不兜底", b.ev("window.__qaChargedCredits({credits:0})"), 0)
        chk("T4 credits=12 原样", b.ev("window.__qaChargedCredits({credits:12})"), 12)
        chk("T4 缺失才用 3.8", b.ev("window.__qaChargedCredits({})"), 3.8)
        chk("T4 非数字也兜底", b.ev("window.__qaChargedCredits({credits:'x'})"), 3.8)

        # ---------- T6 390 移动端 ----------
        b.act("emulate", "--width", "390", "--height", "844", "--dpr", "2", "--mobile")
        time.sleep(0.5)
        b.ev("localStorage.removeItem('jimeng-sessions-v1'), 1")
        b.open("opt4")
        b.act("fill", "--selector", "#prompt", "--value", "__MOCK_SLOW__ 移动端生成态")
        b.act("press", "Enter", "--selector", "#prompt")
        time.sleep(0.6)
        m = b.ev("""JSON.stringify({
          vw: innerWidth,
          docW: document.documentElement.scrollWidth,
          tiles: document.querySelectorAll('.gen-tile').length,
          gridW: (() => { const g = document.querySelector('.gen-grid');
                          return g ? Math.round(g.getBoundingClientRect().width) : -1; })(),
          gridRight: (() => { const g = document.querySelector('.gen-grid');
                              return g ? Math.round(g.getBoundingClientRect().right) : -1; })(),
          pct: document.querySelector('#genPct') ? document.querySelector('#genPct').textContent : null
        })""")
        m = json.loads(m or "{}")
        chk("T6 真实 390 视口", m.get("vw"), 390)
        chk("T6 无横向溢出", m.get("docW", 9999) <= 391, True)
        chk("T6 占位卡在视口内", m.get("gridRight", 9999) <= 390, True)
        chk("T6 百分比角标存在", bool(m.get("pct")), True)
        b.shot("05-generating-mobile")
        for _ in range(60):
            if b.ev("document.querySelectorAll('.result-card, .alert-bar').length > 0"):
                break
            time.sleep(0.2)
        time.sleep(0.3)
        chk("T6 移动端完成后无溢出",
            b.ev("document.documentElement.scrollWidth <= innerWidth + 1"), True)
        b.shot("06-result-mobile")

        rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "base": BASE,
               "passed": sum(1 for r in results if r["ok"]), "total": len(results),
               "checks": results}
        (QA / "optimize-report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
        print(f"\n{rep['passed']}/{rep['total']} passed -> qa/optimize-report.json")
        return 0 if rep["passed"] == rep["total"] else 1
    finally:
        sh("session", "stop", b.s, "--json", timeout=60)


if __name__ == "__main__":
    sys.exit(main())
