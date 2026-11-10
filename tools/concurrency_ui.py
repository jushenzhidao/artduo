#!/usr/bin/env python3
"""并发提交自检：证明页面可以连续提交多个图片任务，而不是「一次只能跑一个」。

前置（零计费）：
  MOCK_PORT=8798 MOCK_DELAY_MS=4000 python3 tools/mock_upstream.py
  PORT=8811 BFL_BASE=http://127.0.0.1:8798 BFL2OPENAI_API_KEY=sk-mock python3 server.py

用法：E2E_BASE=http://127.0.0.1:8811 python3 tools/concurrency_ui.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("E2E_BASE", "http://127.0.0.1:8811")
sys.path.insert(0, str(ROOT / "tools"))

import e2e_bsk as E  # noqa: E402

REPORT = ROOT / "qa" / "concurrency-ui-report.json"
CHECKS: list[dict] = []


def chk(name, got, want=True):
    ok = got == want
    CHECKS.append({"name": name, "ok": ok, "got": got, "want": want})
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  got={got!r} want={want!r}"))


def EV(b, expr, tab=None, attempts=6):
    """进度动画会改 DOM，bsk 的 strict observation 会过期：只读操作多同步几次。"""
    last = None
    for _ in range(attempts):
        try:
            return b.ev(expr, tab=tab)
        except Exception as exc:      # permission_denied: observation changed
            last = exc
            time.sleep(0.25)
    raise last


def state(b, tab):
    return json.loads(EV(b, "JSON.stringify(window.__qaSnapshot())", tab=tab))


def click_once(b, expr, tab):
    """点击只发一次：bsk 的 act 自带重试，对写入动作重试等于重复提交。"""
    r = b.act("evaluate", expr, tab=tab, attempts=1, must=False)
    return r.get("code") not in {"permission_denied", "invalid_params", "action_failed"}


def wait(b, expr, timeout=70, tab=None, interval=0.4):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if b.ev(expr, tab=tab):
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def main() -> int:
    d, rc = E.Bsk("").raw("session", "start", "--json", "--browser", "90d43dd3",
                          "--width", "1700", "--height", "1300", "--no-focus",
                          "--name", "jimeng-concurrency")
    sid = d.get("session_id")
    if not sid:
        print(f"启动会话失败 rc={rc}: {str(d)[:200]}", file=sys.stderr)
        return 2
    b = E.Bsk(sid)
    tab = None
    try:
        tab = b.plain("tab", "create", "--session", sid, "--url", f"{E.BASE}/?theme=dark&qa=freeze",
                      "--json").get("tab_id")
        if not tab:
            print("创建标签页失败", file=sys.stderr)
            return 2
        E.wait_ready(b, tab, timeout=25)
        # 队列/会话都会持久化：先清掉上一轮，保证计数从 0 开始
        b.ev("localStorage.removeItem('jimeng-jobs-v1'), localStorage.removeItem('jimeng-sessions-v1'), 1", tab=tab)
        b.plain("tab", "reload", "--session", sid, str(tab), "--json", timeout=60)
        time.sleep(1.5)
        E.wait_ready(b, tab, timeout=25)
        s0 = state(b, tab)
        base_sessions = s0.get("sessions") or 0
        chk("C0 并发上限来自服务端", s0.get("jobCap"), 4)
        chk("C0 初始空闲", s0.get("jobs"), 0)

        def fill(prompt):
            EV(b, "(() => { const t=document.querySelector('#prompt');"
               f" t.value={json.dumps(prompt)};"
               " t.dispatchEvent(new Event('input',{bubbles:true})); return true; })()", tab=tab)

        def submit(prompt, expect_total):
            """提交后以 jobsTotal 是否增加为准，不信点击返回值（observation 会过期）。"""
            fill(prompt)
            time.sleep(0.15)
            before = state(b, tab).get("jobsTotal", 0)
            click_once(b, "document.querySelector('#sendBtn').click(), true", tab=tab)
            end = time.time() + 12
            while time.time() < end:
                time.sleep(0.35)
                try:
                    if state(b, tab).get("jobsTotal", 0) > before:
                        return True
                except Exception:
                    pass
            return False

        chk("C1 第一个任务提交成功", submit("并发探针 A 一只红色茶杯", 1), True)
        s1 = state(b, tab)
        chk("C1 第一个任务在飞", s1.get("jobs"), 1)
        chk("C1 提交后输入框已清空", EV(b, "document.querySelector('#prompt').value", tab=tab), "")
        chk("C1 输入框没有被锁住", EV(b, "document.querySelector('#prompt').disabled", tab=tab), False)

        fill("并发探针 B 一只蓝色杯子")
        time.sleep(0.2)
        chk("C2 生成中填了新提示词就能发", EV(b, "document.querySelector('#sendBtn').disabled", tab=tab), False)
        chk("C3 第二个任务提交成功", submit("并发探针 B 一只蓝色杯子", 2), True)
        chk("C3 两个任务同时存在", state(b, tab).get("jobs"), 2)

        chk("C4 第三个任务提交成功", submit("并发探针 C 一只绿色盘子", 3), True)
        chk("C4 第四个任务提交成功", submit("并发探针 D 一只黄色碗", 4), True)
        s4 = state(b, tab)
        chk("C4 四个任务同时在飞", s4.get("jobs"), 4)
        chk("C4 队列视图显示 4 个任务块",
            EV(b, "document.querySelectorAll('#conversation [data-job]').length", tab=tab), 4)

        fill("并发探针 E 应该被拒绝")
        time.sleep(0.2)
        # 只有在 4 个都还在飞时，这条才成立；任务跑太快就跳过（不是缺陷）
        if state(b, tab).get("jobs") == 4:
            chk("C5 并发满时发送按钮禁用",
                EV(b, "document.querySelector('#sendBtn').disabled", tab=tab), True)
            click_once(b, "document.querySelector('#sendBtn').click(), true", tab=tab)
            time.sleep(0.9)
            s5 = state(b, tab)
            chk("C5 第 5 个任务没有开始跑", s5.get("jobs"), 4)
        else:
            print("SKIP  C5：前面的任务已完成，并发窗口已过")
        EV(b, "(() => { const t=document.querySelector('#prompt'); t.value='';"
              " t.dispatchEvent(new Event('input',{bubbles:true})); return true; })()", tab=tab)

        # 4 个请求并行，mock 每个 4s；串行会是 16s+
        t0 = time.time()
        ok = wait(b, "window.__qaSnapshot().jobs === 0", timeout=70, tab=tab)
        elapsed = time.time() - t0
        chk("C6 四个任务全部完成", ok, True)
        chk("C6 墙钟明显短于串行 16s", elapsed < 12, True)
        print(f"      （4 个任务总耗时 {elapsed:.1f}s）")
        cards = EV(b, "document.querySelectorAll('#conversation .result-card').length", tab=tab)
        chk("C7 队列里出现 4 张结果", cards, 4)
        s7 = state(b, tab)
        chk("C7 会话记录正好新增 4 条", (s7.get("sessions") or 0) - base_sessions, 4)
        chk("C7 结束后回到空闲", s7.get("busy"), False)
        chk("C7 无横向溢出", EV(b, "document.documentElement.scrollWidth > innerWidth + 1", tab=tab), False)
    finally:
        try:
            if tab:
                b.plain("tab", "close", "--session", sid, str(tab), "--json", timeout=60)
        finally:
            b.plain("session", "stop", sid, "--json", timeout=60)

    passed = sum(1 for c in CHECKS if c["ok"])
    REPORT.write_text(json.dumps({"passed": passed, "total": len(CHECKS), "checks": CHECKS},
                                 ensure_ascii=False, indent=1))
    print(f"\n{passed}/{len(CHECKS)} passed -> {REPORT}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
