#!/usr/bin/env python3
"""任务队列自检：提交不阻塞、真并发、重试带抖动、幂等不多花钱。

自起三个进程（零计费）：

  8781  mock flux    MOCK_DELAY_MS=800  MOCK_FLAKY_FAILS=2
  8782  主实例       WORKERS=4 QUEUE_MAX=16 RETRIES=2（backoff 0.15/0.6，抖动 1.0）
  8783  窄实例       WORKERS=1 QUEUE_MAX=2  RETRIES=0（验证队列上限与取消）

用例：

  T1 提交即返回：上游要 800ms，POST /api/tasks 必须在 300ms 内回 queued
  T2 真并发：一次提交 6 个，4 个 worker 两波跑完（墙钟 < 串行的 60%），上游恰好 6 次
  T3 队列上限：窄实例连提 4 个 → 至少一个 429（队列只有 2 格）
  T4 幂等（异步）：同 Idempotency-Key 连提两次 → 同一个任务 id，上游只多 1 次
  T5 幂等（同步）：/api/generate 同 key 第二次 replay=true，不再打上游
  T6 重试：__MOCK_FLAKY__（前 2 次 503）→ attempts=3 且最终成功
  T7 不重试：__MOCK_400__ → attempts=1 立刻失败（4xx 不该重试）
  T8 取消：窄实例排队中的任务可取消，且上游从未收到它
  T9 页面：点「生成本集」后 300ms 内 6 张全部进入排队态，随后全部出图
  T10 status：workers / queueMax / retries / tasks.counts 如实上报
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
SHOTS = QA / "shots" / "tasks"
SHOTS.mkdir(parents=True, exist_ok=True)
video_check.SHOTS = SHOTS
REPORT = QA / "tasks-report.json"

MOCK = "http://127.0.0.1:8781"
FAST = "http://127.0.0.1:8782"
NARROW = "http://127.0.0.1:8783"
DELAY = 0.8
RECORD = Path("/tmp/tasks-mock.jsonl")

checks: list[tuple[str, bool, object, object]] = []


def chk(name, got, want):
    checks.append((name, got == want, got, want))
    print(("PASS  " if got == want else "FAIL  ") + name
          + ("" if got == want else f"   got={got!r} want={want!r}"))


def spawn(port: int, env_extra: dict, script: str = "server.py") -> subprocess.Popen:
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                         capture_output=True, text=True).stdout
    for pid in out.split():
        subprocess.run(["kill", "-9", pid], capture_output=True)
    time.sleep(0.3)
    env = dict(os.environ, **{"PORT": str(port)}, **env_extra)
    proc = subprocess.Popen([sys.executable, str(ROOT / script)], cwd=str(ROOT),
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                if r.status == 200:
                    return proc
        except urllib.error.HTTPError:
            return proc            # mock 没有 /api/health，能回 404 就说明在听
        except Exception:
            time.sleep(0.15)
    raise RuntimeError(f"{port} 起不来")


def req(base: str, path: str, body=None, idem: str | None = None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if idem:
        headers["Idempotency-Key"] = idem
    r = urllib.request.Request(base + path, data=data, method="POST" if data else "GET",
                               headers=headers)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    try:
        with op.open(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}"), time.time() - t0
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}"), time.time() - t0


def status(base: str) -> dict:
    return req(base, "/api/status")[1]


def wait_task(base: str, tid: str, timeout=30) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        _, d, _ = req(base, f"/api/tasks/{tid}")
        t = d.get("task") or {}
        if t.get("status") in ("succeeded", "failed", "canceled"):
            return t
        time.sleep(0.12)
    return {"status": "timeout"}


def records() -> list[dict]:
    if not RECORD.exists():
        return []
    return [json.loads(l) for l in RECORD.read_text(encoding="utf-8").splitlines() if l.strip()]


def marker(prompt: str) -> str:
    return str(int(time.time() * 1000)) + "-" + str(abs(hash(prompt)) % 10000)


def main() -> int:
    RECORD.unlink(missing_ok=True)
    mock = spawn(8781, {"MOCK_PORT": "8781", "MOCK_RECORD": str(RECORD),
                        "MOCK_DELAY_MS": str(int(DELAY * 1000)), "MOCK_FLAKY_FAILS": "2"},
                 script="tools/mock_upstream.py")
    fast = spawn(8782, {"JIMENG_LLM": "off", "BFL_BASE": MOCK, "BFL2OPENAI_API_KEY": "mock-key",
                        "JIMENG_CONFIG_FILE": "/tmp/tasks-a.json", "JIMENG_MAX_CALLS": "200",
                        "JIMENG_WORKERS": "4", "JIMENG_QUEUE_MAX": "16",
                        "JIMENG_RETRIES": "2", "JIMENG_BACKOFF_BASE": "0.15",
                        "JIMENG_BACKOFF_CAP": "0.6", "JIMENG_JITTER": "1.0",
                        "JIMENG_DISPATCH_JITTER": "0.02",
                        "AVM_COOKIE_FILE": "/tmp/no-such-cookie.txt",
                        "AVM_MINTER_URL": "http://127.0.0.1:39999"})
    narrow = spawn(8783, {"JIMENG_LLM": "off", "BFL_BASE": MOCK, "BFL2OPENAI_API_KEY": "mock-key",
                          "JIMENG_CONFIG_FILE": "/tmp/tasks-b.json", "JIMENG_MAX_CALLS": "50",
                          "JIMENG_WORKERS": "1", "JIMENG_QUEUE_MAX": "2",
                          "JIMENG_RETRIES": "0", "JIMENG_DISPATCH_JITTER": "0",
                          "AVM_COOKIE_FILE": "/tmp/no-such-cookie.txt",
                          "AVM_MINTER_URL": "http://127.0.0.1:39999"})
    try:
        st = status(FAST)
        chk("T10 workers=4", st.get("workers"), 4)
        chk("T10 queueMax=16", st.get("queueMax"), 16)
        chk("T10 retries=2", st.get("retries"), 2)
        chk("T10 tasks 计数存在", isinstance(st.get("tasks", {}).get("counts"), dict), True)

        # ---------- T1 提交即返回 ----------
        m1 = marker("t1")
        code, d, took = req(FAST, "/api/tasks", {"prompt": f"雨夜车库 {m1}"})
        chk("T1 提交返回 202", code, 202)
        chk("T1 立刻拿到 queued", (d.get("status"), bool(d.get("id"))), ("queued", True))
        chk("T1 提交耗时远小于上游耗时", took < DELAY * 0.45, True)
        chk("T1 任务最终成功", wait_task(FAST, d["id"]).get("status"), "succeeded")

        # ---------- T2 真并发 ----------
        before = len(records())
        t0 = time.time()
        ids = []
        for i in range(6):
            c, r, _ = req(FAST, "/api/tasks", {"prompt": f"批量并发 {marker('t2')} #{i}"})
            if r.get("id"):
                ids.append(r["id"])
        wall = time.time() - t0
        chk("T2 6 个都受理", len(ids), 6)
        finals = [wait_task(FAST, i) for i in ids]
        chk("T2 6 个都成功", [f.get("status") for f in finals].count("succeeded"), 6)
        chk("T2 上游恰好 6 次", len(records()) - before, 6)
        chk("T2 墙钟明显短于串行", wall < DELAY * 6 * 0.6, True)
        st = status(FAST)
        chk("T2 峰值不超 4 路", st.get("inflightPeak", 0) <= 4, True)
        chk("T2 峰值确实并发", st.get("inflightPeak", 0) >= 2, True)

        # ---------- T4 幂等（异步） ----------
        key = f"idem-{time.time()}"
        m4 = marker("t4")
        before = len(records())
        c1, r1, _ = req(FAST, "/api/tasks", {"prompt": f"幂等探针 {m4}"}, idem=key)
        c2, r2, _ = req(FAST, "/api/tasks", {"prompt": f"幂等探针 {m4}"}, idem=key)
        chk("T4 两次都成功受理", (c1 in (200, 202), c2 in (200, 202)), (True, True))
        chk("T4 同一个任务 id", r1.get("id"), r2.get("id"))
        chk("T4 第二次标为去重", r2.get("deduped"), True)
        wait_task(FAST, r1["id"])
        time.sleep(0.3)
        chk("T4 上游只打了 1 次", len(records()) - before, 1)

        # ---------- T5 幂等（同步接口） ----------
        before = len(records())
        k5 = f"sync-{time.time()}"
        c1, r1, _ = req(FAST, "/api/generate", {"prompt": f"同步幂等 {marker('t5')}"}, idem=k5)
        c2, r2, _ = req(FAST, "/api/generate", {"prompt": f"同步幂等 {marker('t5')}"}, idem=k5)
        chk("T5 第一次正常出图", (c1, r1.get("ok")), (200, True))
        chk("T5 第二次是重放", (c2, r2.get("replay"), r2.get("deduped")), (200, True, True))
        chk("T5 上游只打了 1 次", len(records()) - before, 1)

        # ---------- T6 重试（503 两次后成功） ----------
        before = len(records())
        c6, r6, _ = req(FAST, "/api/tasks", {"prompt": f"__MOCK_FLAKY__ 抖动重试 {marker('t6')}"})
        t6 = wait_task(FAST, r6.get("id"), timeout=40)
        chk("T6 重试后成功", t6.get("status"), "succeeded")
        chk("T6 一共试了 3 次", t6.get("attempts"), 3)
        chk("T6 上游确实收到 3 次", len(records()) - before, 3)
        chk("T6 成功的产物有图", bool((t6.get("result") or {}).get("images")), True)

        # ---------- T7 4xx 不重试 ----------
        before = len(records())
        c7, r7, _ = req(FAST, "/api/tasks", {"prompt": f"__MOCK_400__ 不该重试 {marker('t7')}"})
        t7 = wait_task(FAST, r7.get("id"), timeout=30)
        chk("T7 失败收场", t7.get("status"), "failed")
        chk("T7 只试了 1 次", t7.get("attempts"), 1)
        chk("T7 记下上游状态码", t7.get("upstream_http"), 400)
        chk("T7 上游只收到 1 次", len(records()) - before, 1)

        # ---------- T3 队列上限（窄实例） ----------
        codes = []
        for i in range(4):
            c, r, _ = req(NARROW, "/api/tasks", {"prompt": f"窄队列 {marker('t3')} #{i}"})
            codes.append(c)
        chk("T3 队列满时返回 429", 429 in codes, True)
        chk("T3 至少受理 2 个（1 个在跑 + 队列 2 格）",
            len([c for c in codes if c == 202]) >= 2, True)

        # ---------- T8 取消排队中的任务 ----------
        for _ in range(80):
            q = status(NARROW).get("tasks", {}).get("counts", {})
            if q.get("queued", 0) == 0 and q.get("running", 0) == 0:
                break
            time.sleep(0.3)
        m8 = marker("t8")
        c, busy, _ = req(NARROW, "/api/tasks", {"prompt": f"占住 worker {m8}"})
        c, q1, _ = req(NARROW, "/api/tasks", {"prompt": f"排队待取消 {m8}-x"})
        c, q2, _ = req(NARROW, "/api/tasks", {"prompt": f"排队待取消 {m8}-y"})
        tid = q2.get("id") or q1.get("id")
        chk("T8 拿到排队任务", bool(tid), True)
        code, cancel, _ = req(NARROW, f"/api/tasks/{tid}/cancel", {})
        chk("T8 取消受理", (code, cancel.get("status")), (200, "canceled"))
        wait_task(NARROW, tid)
        time.sleep(0.4)
        chk("T8 上游从未收到被取消的提示词",
            any(f"{m8}-y" in json.dumps(r.get("body") or {}, ensure_ascii=False) for r in records()), False)

        # ---------- T9 页面：点批量即返回排队态 ----------
        b = B()
        try:
            b.open(FAST, "tasks-ui")
            chk("T9 分镜表就绪", b.make_script(), True)
            t0 = time.time()
            b.js("document.querySelector('#sbAll').click()")
            time.sleep(0.28)
            st_page = b.evj("window.__qaStoryboard()")
            chk("T9 300ms 内全部已提交", (st_page.get("queued", 0) + st_page.get("running", 0)), 6)
            chk("T9 没有卡在未提交", b.ev("document.querySelectorAll('.sb-card[data-status=\"idle\"]').length"), 0)
            chk("T9 按钮进入生成中", "生成中" in (b.ev("document.querySelector('#sbAll').textContent") or ""), True)
            b.shot("01-submitted")
            for _ in range(120):
                if not b.evj("window.__qaStoryboard().busy"):
                    break
                time.sleep(0.3)
            st_done = b.evj("window.__qaStoryboard()")
            chk("T9 6 张全出", st_done.get("done"), 6)
            chk("T9 任务 id 已清空", all(x is None for x in (st_done.get("taskIds") or [])), True)
            b.shot("02-all-done")
        finally:
            subprocess.run(["bsk", "session", "stop", "--session", b.s, "--json"],
                           capture_output=True)
    finally:
        for proc in (mock, fast, narrow):
            proc.terminate()
            try:
                proc.wait(timeout=6)
            except Exception:
                proc.kill()

    passed = sum(1 for c in checks if c[1])
    REPORT.write_text(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "passed": passed, "total": len(checks),
        "items": [{"name": n, "ok": ok, "got": g, "want": w} for n, ok, g, w in checks],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{passed}/{len(checks)} passed -> {REPORT.relative_to(ROOT)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
