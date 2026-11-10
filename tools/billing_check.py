#!/usr/bin/env python3
"""积分计费 ↔ new-api api-key 联动自检（零真实计费）。

链路：浏览器/API → server.py（冻结/结算）→ mock 上游（new-api billing 契约 + 出图）

覆盖：
  T1  换汇口径：500000 quota = $1 = 100 积分；$0.038 = 3.8 积分
  T2  上游额度读取：hard_limit_usd 是总额度，剩余 = 总额 - usage/100
  T3  提交即冻结：available = 上游剩余 - held
  T4  成功结算：used 增加、held 归零、流水 settle
  T5  幂等重放：同一 Idempotency-Key 不重复冻结/扣费
  T6  失败释放：上游 400 → release，不扣额度
  T7  余额不足：402 + code=insufficient_credits，且不产生新冻结
  T8  同步生成 /api/generate 也走冻结/结算
  T9  取消排队任务：释放冻结（单 worker 保证第二个还在队列）
  T10 本地账本模式：余额扣减、不足拒绝、重启后余额保留
  T11 上游无 billing 端点：自动退化本地账本，不假装读到额度
  T12 任何响应都不含明文 sk- key

只用标准库；所有实例只监听 127.0.0.1，端口动态分配，绝不碰真实上游。
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
QA.mkdir(parents=True, exist_ok=True)
REPORT = QA / "billing-report.json"
TMP = Path("/tmp/jimeng-billing-check")
TMP.mkdir(parents=True, exist_ok=True)

checks: list[tuple[str, bool, object, object]] = []
procs: list[tuple[subprocess.Popen, object]] = []


def chk(name: str, got, want) -> None:
    ok = got == want
    checks.append((name, ok, got, want))
    print(("PASS  " if ok else "FAIL  ") + name
          + ("" if ok else f"   got={got!r} want={want!r}"), flush=True)


def chk_true(name: str, got, hint: str = "") -> None:
    ok = bool(got)
    checks.append((name, ok, got, True))
    print(("PASS  " if ok else "FAIL  ") + name
          + ("" if ok else f"   got={got!r} {hint}"), flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http(method: str, url: str, body: dict | None = None,
         headers: dict | None = None, timeout: float = 20) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    try:
        return status, json.loads(raw.decode("utf-8"))
    except Exception:
        return status, {"_raw": raw.decode("utf-8", "replace")[:300]}


def wait_http(url: str, timeout: float = 10) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with opener.open(urllib.request.Request(url), timeout=0.5) as r:
                if r.status < 500:
                    return True
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.15)
    return False


def spawn(cmd: list[str], env: dict, log_name: str) -> subprocess.Popen:
    log = open(TMP / log_name, "wb")
    full = {**os.environ, **env}
    # 不让测试进程继承外部代理：带 Authorization 的请求会被代理吞掉
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        full.pop(k, None)
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=full,
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    procs.append((p, log))
    return p


def stop(p: subprocess.Popen) -> None:
    if p.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    except Exception:
        p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            p.kill()


def start_mock(port: int, billing: bool = True, total: float = 1.0,
               delay_ms: int = 400) -> subprocess.Popen:
    env = {"MOCK_PORT": str(port), "MOCK_RECORD": str(TMP / f"mock-{port}.jsonl"),
           "MOCK_DELAY_MS": str(delay_ms), "MOCK_BILLING": "1" if billing else "0",
           "MOCK_BILLING_TOTAL_USD": str(total)}
    p = spawn([sys.executable, "tools/mock_upstream.py"], env, f"mock-{port}.log")
    assert wait_http(f"http://127.0.0.1:{port}/__stats"), f"mock {port} 未就绪"
    return p


def start_server(port: int, mock_port: int, tag: str, mode: str = "auto",
                 start_credits: float = 2000, workers: int = 4,
                 concurrency: int = 4) -> subprocess.Popen:
    db = TMP / f"billing-{tag}.db"
    cfg = TMP / f"config-{tag}.json"
    for f in (db, cfg):
        if f.exists():
            f.unlink()
    env = {
        "PORT": str(port),
        "BFL_BASE": f"http://127.0.0.1:{mock_port}",
        "BFL2OPENAI_API_KEY": "sk-mock-billing",
        "JIMENG_REAL": "1",
        "JIMENG_CONFIG_FILE": str(cfg),
        "JIMENG_BILLING_DB": str(db),
        "JIMENG_BILLING_MODE": mode,
        "JIMENG_START_CREDITS": str(start_credits),
        "JIMENG_MAX_CALLS": "200",
        "JIMENG_WORKERS": str(workers),
        "JIMENG_CONCURRENCY": str(concurrency),
        "JIMENG_BILLING_TIMEOUT": "2",
    }
    p = spawn([sys.executable, "server.py"], env, f"server-{tag}.log")
    assert wait_http(f"http://127.0.0.1:{port}/api/health"), f"server {tag} 未就绪"
    return p


def billing(port: int, force: bool = False) -> dict:
    return http("GET", f"http://127.0.0.1:{port}/api/billing"
                       f"{'?force=1' if force else ''}")[1]


def submit(port: int, prompt: str, idem: str | None = None) -> tuple[int, dict]:
    headers = {"Idempotency-Key": idem} if idem else None
    return http("POST", f"http://127.0.0.1:{port}/api/tasks",
                {"prompt": prompt}, headers)


def wait_task(port: int, tid: str, timeout: float = 25) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, data = http("GET", f"http://127.0.0.1:{port}/api/tasks/{tid}")
        last = data.get("task") or {}
        if last.get("status") in ("succeeded", "failed", "canceled"):
            return last
        time.sleep(0.15)
    return last


def settle_entries(b: dict) -> list[dict]:
    return [e for e in (b.get("ledger") or []) if e.get("kind") == "settle"]


def main() -> int:
    print(f"[billing-check] root={ROOT}", flush=True)

    # ---------- T1 纯函数口径 ----------
    sys.path.insert(0, str(ROOT))
    import billing as B
    chk("T1 500000 quota = 100 积分", B.quota_to_credits(500000), 100.0)
    chk("T1 $0.038 = 3.8 积分", B.usd_to_credits(0.038), 3.8)
    chk("T1 3.8 积分 = $0.038", B.credits_to_usd(3.8), 0.038)

    # ---------- 主链路：mock 有 billing，server auto ----------
    mock_port = free_port()
    srv_port = free_port()
    mock = start_mock(mock_port, billing=True, total=1.0, delay_ms=400)
    srv = start_server(srv_port, mock_port, "main")
    base = f"http://127.0.0.1:{srv_port}"

    b0 = billing(srv_port, force=True)
    chk("T2 识别 new-api 额度", b0.get("source"), "new-api")
    chk("T2 总额度 100 积分", b0.get("total"), 100.0)
    chk("T2 初始可用 100", b0.get("available"), 100.0)
    chk("T2 无冻结", b0.get("held"), 0.0)

    code, sub = submit(srv_port, "计费成功路径", "bill-ok-1")
    chk("T3 提交返回 202", code, 202)
    tid = sub.get("id")
    chk_true("T3 返回任务 id", tid)
    b1 = billing(srv_port)
    chk("T3 提交后冻结 3.8", b1.get("held"), 3.8)
    chk("T3 可用同步减 3.8", b1.get("available"), 96.2)

    t = wait_task(srv_port, tid or "")
    chk("T4 任务成功", t.get("status"), "succeeded")
    b2 = billing(srv_port, force=True)
    chk("T4 结算后 held=0", b2.get("held"), 0.0)
    chk("T4 上游已用 3.8", b2.get("used"), 3.8)
    chk("T4 可用 96.2", b2.get("available"), 96.2)
    chk("T4 今日消耗 3.8", b2.get("todaySpent"), 3.8)
    chk("T4 一笔结算流水", len(settle_entries(b2)), 1)

    # T5 幂等：同一个 key 再提交，不新增冻结/流水
    code, again = submit(srv_port, "计费成功路径", "bill-ok-1")
    chk("T5 重复提交不报错", code, 200)
    chk("T5 命中同一任务", again.get("id"), tid)
    chk("T5 deduped=true", again.get("deduped"), True)
    b3 = billing(srv_port, force=True)
    chk("T5 不重复扣费", b3.get("used"), 3.8)
    chk("T5 流水条数不变", len(settle_entries(b3)), 1)

    # T6 失败释放
    code, sub = submit(srv_port, "__MOCK_400__ 失败不计费")
    chk("T6 失败任务已入队", code, 202)
    t = wait_task(srv_port, sub.get("id") or "")
    chk("T6 任务失败", t.get("status"), "failed")
    b4 = billing(srv_port, force=True)
    chk("T6 失败后 held=0", b4.get("held"), 0.0)
    chk("T6 失败不扣额度", b4.get("used"), 3.8)
    chk("T6 有 release 流水", (b4["ledger"][0] or {}).get("kind"), "release")

    # T7 余额不足：总额调到 $0.03（3 积分，已用 3.8）
    http("POST", f"http://127.0.0.1:{mock_port}/__billing/total", {"total_usd": 0.03})
    http("POST", f"{base}/api/billing/sync", {})
    b5 = billing(srv_port)
    chk("T7 上游剩余归零", b5.get("available"), 0.0)
    before = len(b5.get("ledger") or [])
    code, denied = submit(srv_port, "余额不足必须拒绝")
    chk("T7 HTTP 402", code, 402)
    chk("T7 错误码", denied.get("code"), "insufficient_credits")
    chk("T7 给出所需积分", denied.get("need"), 3.8)
    chk("T7 没有新增冻结", len(billing(srv_port).get("ledger") or []), before)

    # T8 恢复额度，同步生成也走冻结/结算
    http("POST", f"http://127.0.0.1:{mock_port}/__billing/total", {"total_usd": 1.0})
    http("POST", f"{base}/api/billing/sync", {})
    code, gen = http("POST", f"{base}/api/generate", {"prompt": "同步生成计费"})
    chk("T8 同步生成 200", code, 200)
    chk("T8 返回 3.8 积分", gen.get("credits"), 3.8)
    b6 = billing(srv_port, force=True)
    chk("T8 上游累计 7.6", b6.get("used"), 7.6)
    chk("T8 held 归零", b6.get("held"), 0.0)
    chk_true("T8 同步任务有 settle 流水",
             any((e.get("task_id") or "").startswith("sync-")
                 for e in settle_entries(b6)))

    # T9 取消排队任务释放冻结：单 worker + 慢上游
    cancel_port = free_port()
    cancel_mock_port = free_port()
    start_mock(cancel_mock_port, billing=True, total=1.0, delay_ms=1200)
    start_server(cancel_port, cancel_mock_port, "cancel", workers=1, concurrency=1)
    cbase = f"http://127.0.0.1:{cancel_port}"
    _, first = submit(cancel_port, "占住唯一 worker")
    chk_true("T9 第一个任务入队", first.get("id"))
    time.sleep(0.35)                             # 让 worker 进入 running
    _, second = submit(cancel_port, "排队后取消")
    chk_true("T9 第二个任务入队", second.get("id"))
    code, canceled = http("POST", f"{cbase}/api/tasks/{second.get('id')}/cancel", {})
    chk("T9 取消返回 200", code, 200)
    chk("T9 状态 canceled", canceled.get("status"), "canceled")
    bc = billing(cancel_port)
    chk("T9 取消后只保留在飞任务的冻结", bc.get("held"), 3.8)
    wait_task(cancel_port, first.get("id") or "")
    bc2 = billing(cancel_port, force=True)
    chk("T9 全部结束后 held=0", bc2.get("held"), 0.0)

    # ---------- T10 本地账本模式（上游没有 billing 也照常记） ----------
    local_port = free_port()
    start_server(local_port, mock_port, "local", mode="local", start_credits=6)
    lbase = f"http://127.0.0.1:{local_port}"
    bl = billing(local_port)
    chk("T10 本地模式来源", bl.get("source"), "local-ledger")
    chk("T10 初始 6", bl.get("available"), 6.0)
    code, _ = http("POST", f"{lbase}/api/generate", {"prompt": "本地账本第一张"})
    chk("T10 第一张成功", code, 200)
    bl2 = billing(local_port)
    chk("T10 扣 3.8 剩 2.2", bl2.get("available"), 2.2)
    chk("T10 账本余额同步", bl2.get("balance"), 2.2)
    code, denied = http("POST", f"{lbase}/api/generate", {"prompt": "本地账本第二张"})
    chk("T10 余额不足 402", code, 402)
    chk("T10 可用 2.2", denied.get("available"), 2.2)

    # 重启同一 DB：余额与流水都在（进程重启不重置账本）
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{local_port}", "-sTCP:LISTEN", "-t"],
                         capture_output=True, text=True).stdout.strip().splitlines()
    if out:
        os.kill(int(out[0]), signal.SIGTERM)
        time.sleep(0.4)
    # 复用同一个 tag 会删库，这里直接起第二个进程指向同一 DB
    env = {
        "PORT": str(local_port), "BFL_BASE": f"http://127.0.0.1:{mock_port}",
        "BFL2OPENAI_API_KEY": "sk-mock-billing", "JIMENG_REAL": "1",
        "JIMENG_CONFIG_FILE": str(TMP / "config-local2.json"),
        "JIMENG_BILLING_DB": str(TMP / "billing-local.db"),
        "JIMENG_BILLING_MODE": "local", "JIMENG_START_CREDITS": "50",
        "JIMENG_MAX_CALLS": "200", "JIMENG_BILLING_TIMEOUT": "2",
    }
    spawn([sys.executable, "server.py"], env, "server-local2.log")
    wait_http(f"{lbase}/api/health")
    bl3 = billing(local_port, force=True)
    chk("T10 重启后余额仍 2.2", bl3.get("available"), 2.2)
    chk_true("T10 重启后流水仍在", len(bl3.get("ledger") or []) >= 2)

    # ---------- T11 上游没有 billing 端点 → 自动退化 ----------
    nobill_port = free_port()
    nobill_srv_port = free_port()
    start_mock(nobill_port, billing=False, delay_ms=100)
    start_server(nobill_srv_port, nobill_port, "nobill", mode="auto", start_credits=50)
    bn = billing(nobill_srv_port, force=True)
    chk("T11 退化本地展示", bn.get("source"), "local-ledger")
    chk("T11 supported=false", bn.get("supported"), False)
    chk("T11 不拦截（shadow）", bn.get("billingMode"), "shadow")
    chk("T11 enforce=false", bn.get("enforce"), False)
    chk_true("T11 说明原因", "404" in str((bn.get("upstream") or {}).get("reason")),
             str(bn.get("upstream")))
    code, gen = http("POST", f"http://127.0.0.1:{nobill_srv_port}/api/generate",
                     {"prompt": "无 billing 端点也不拦生成"})
    chk("T11 生成成功", code, 200)
    chk("T11 演示余额不变", billing(nobill_srv_port).get("available"), 50.0)

    # ---------- T12 不泄露密钥 ----------
    blob = json.dumps([b0, b1, b2, b3, b4, b5, b6, bl, bl2, bl3, bn], ensure_ascii=False)
    chk("T12 响应不含明文 key", "sk-mock-billing" in blob, False)

    failed = [c for c in checks if not c[1]]
    report = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "passed": len(checks) - len(failed), "total": len(checks),
        "checks": [{"name": n, "ok": ok, "got": got, "want": want}
                   for n, ok, got, want in checks],
        "ports": {"main": [mock_port, srv_port], "cancel": cancel_port,
                  "local": local_port, "nobill": nobill_srv_port},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passed · report={REPORT}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        for p, log in reversed(procs):
            stop(p)
            try:
                log.close()
            except Exception:
                pass
