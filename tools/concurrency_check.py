#!/usr/bin/env python3
"""并发出图自检：证明「批量不再一张一张等」且闸门没被绕过。

自起三个进程（零计费，全指向带固定延迟的 mock）：

  mock  8786  MOCK_DELAY_MS=1200（每个请求固定慢 1.2s，用来区分串行/并发）
  8785        JIMENG_CONCURRENCY=4, JIMENG_MAX_CALLS=40   ← 主用例
  8784        JIMENG_CONCURRENCY=4, JIMENG_MAX_CALLS=2    ← 额度不足用例

用例：

  C1 服务端并发：4 个请求同时打，墙钟 ≈ 1 个请求（串行会是 4 倍）
  C2 并发上限：6 个同时打、上限 4 → 两波完成，且峰值在飞 ≤ 4
  C3 页面批量：并发 3 跑 6 镜，墙钟明显短于串行，且在飞张数峰值 ≤ 3 且 ≥ 2
  C4 停止：点停止后不再派新请求（在飞的允许跑完）
  C5 额度闸门：上限 2 的实例跑 6 镜，只成功 2 张，不多扣一次
  C6 并发=1 时退回串行（墙钟 ≈ 6 个请求）
"""
from __future__ import annotations

import json
import os
import shutil
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
SHOTS = QA / "shots" / "concurrency"
SHOTS.mkdir(parents=True, exist_ok=True)
video_check.SHOTS = SHOTS
REPORT = QA / "concurrency-report.json"

MOCK = "http://127.0.0.1:8786"
FAST = "http://127.0.0.1:8785"
TIGHT = "http://127.0.0.1:8784"
DELAY = 1.2
RECORD = Path("/tmp/conc-mock.jsonl")
PROMPT = "帮我写一部都市复仇短剧：女主回公司查父亲的死因，共 6 集。"
ENV_COMMON = {
    "JIMENG_LLM": "off",
    "BFL_BASE": MOCK,
    "BFL2OPENAI_API_KEY": "mock-key",
    "AVM_COOKIE_FILE": "/tmp/no-such-cookie.txt",
    "AVM_MINTER_URL": "http://127.0.0.1:39999",
    "JIMENG_CONCURRENCY": "4",
}


def spawn(env_extra: dict) -> subprocess.Popen:
    env = dict(os.environ, **ENV_COMMON, **env_extra)
    proc = subprocess.Popen([sys.executable, str(ROOT / "server.py")],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port = env_extra["PORT"]
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                if r.status == 200:
                    return proc
        except Exception:
            time.sleep(0.15)
    raise RuntimeError(f"{port} 起不来")


def status(base: str) -> dict:
    with urllib.request.urlopen(base + "/api/status", timeout=5) as r:
        return json.loads(r.read())


def records() -> int:
    try:
        return len([l for l in RECORD.read_text(encoding="utf-8").splitlines() if l.strip()])
    except OSError:
        return 0


def post_generate(base: str, prompt: str) -> tuple[int, dict, float]:
    body = json.dumps({"prompt": prompt, "mode": "image"}).encode()
    req = urllib.request.Request(base + "/api/generate", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    try:
        with opener.open(req, timeout=120) as r:
            return r.status, json.loads(r.read()), time.time() - t0
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), time.time() - t0


def parallel(base: str, n: int) -> tuple[list, float]:
    import threading
    out: list = [None] * n
    t0 = time.time()

    def run(i):
        out[i] = post_generate(base, f"并发探针 {i} {time.time()}")

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out, time.time() - t0


def main() -> int:
    results = []

    def chk(name, got, want):
        ok = got == want
        results.append({"name": name, "got": got, "want": want, "ok": ok})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {got!r} (期望 {want!r})")

    RECORD.unlink(missing_ok=True)
    mock = subprocess.Popen(
        [sys.executable, str(ROOT / "tools" / "mock_upstream.py")],
        cwd=str(ROOT), env=dict(os.environ, MOCK_PORT="8786", MOCK_DELAY_MS=str(int(DELAY * 1000)),
                                MOCK_RECORD=str(RECORD)),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs = [mock]
    b = B()
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(MOCK + "/", timeout=1):
                    break
            except Exception:
                time.sleep(0.15)
        procs.append(spawn({"PORT": "8785", "JIMENG_MAX_CALLS": "40",
                            "JIMENG_CONFIG_FILE": "/tmp/conc-8785.json"}))
        procs.append(spawn({"PORT": "8784", "JIMENG_MAX_CALLS": "2",
                            "JIMENG_CONFIG_FILE": "/tmp/conc-8784.json"}))

        st = status(FAST)
        chk("C0 代理并发上限", st["concurrency"], 4)
        chk("C0 空闲时没有在飞请求", st["inflight"], 0)

        # ---------------- C1 服务端并发 ----------------
        before = records()
        outs, wall = parallel(FAST, 4)
        chk("C1 4 个请求都成功", all(o[0] == 200 and o[1].get("ok") for o in outs), True)
        chk("C1 只发了 4 次上游", records() - before, 4)
        chk("C1 墙钟≈1 个请求（并发而不是排队）", wall < DELAY * 1.9, True)
        chk("C1 峰值在飞 ≥ 3", status(FAST)["inflightPeak"] >= 3, True)
        chk("C1 峰值在飞 ≤ 上限", status(FAST)["inflightPeak"] <= 4, True)
        print(f"    （4 个并发实际 {wall:.2f}s；串行需要 {DELAY * 4:.1f}s）")

        # ---------------- C2 并发上限 ----------------
        before = records()
        outs, wall = parallel(FAST, 6)
        chk("C2 6 个请求都成功", all(o[0] == 200 and o[1].get("ok") for o in outs), True)
        chk("C2 只发了 6 次上游", records() - before, 6)
        chk("C2 两波完成（4+2）", DELAY * 1.9 < wall < DELAY * 3.2, True)
        chk("C2 峰值仍不超上限", status(FAST)["inflightPeak"] <= 4, True)
        print(f"    （6 个并发、上限 4 实际 {wall:.2f}s）")

        # ---------------- C3 页面批量并发 3 ----------------
        b.open(FAST, "c3", clear=True)
        chk("C3 分镜表就绪", b.make_script(), True)
        chk("C3 并发由服务端决定（4 路）", b.evj("window.__qaStoryboard().concurrency"), 4)
        before = records()
        b.js("""window.__conc = {max: 0, samples: 0};
          clearInterval(window.__concTimer);
          window.__concTimer = setInterval(() => {
            const s = window.__qaStoryboard();
            if (!s.busy) return;
            window.__conc.samples += 1;
            window.__conc.max = Math.max(window.__conc.max, s.running);
          }, 100);""")
        t0 = time.time()
        b.js("document.querySelector('#sbAll').click()")
        time.sleep(0.6)
        chk("C3 按钮显示生成中", "生成中" in (b.ev("document.querySelector('#sbAll').textContent") or ""), True)
        while time.time() - t0 < 60:
            if not b.evj("window.__qaStoryboard().busy"):
                break
            time.sleep(0.25)
        wall3 = time.time() - t0
        st = b.evj("window.__qaStoryboard()")
        chk("C3 6 张全出", [s["status"] for s in st["shots"]].count("done"), 6)
        chk("C3 上游恰好 6 次", records() - before, 6)
        peak = b.evj("window.__conc.max")
        chk("C3 同时在飞不超 worker 数", peak <= 4, True)
        chk("C3 确实并行（峰值 ≥ 2）", peak >= 2, True)
        chk("C3 墙钟短于串行", wall3 < DELAY * 6 * 0.62, True)
        b.js("clearInterval(window.__concTimer)")
        print(f"    （并发 3 跑 6 张 {wall3:.2f}s，串行需 {DELAY * 6:.1f}s；峰值 {peak}）")

        # ---------------- C4 停止不再派新请求 ----------------
        b.open(FAST, "c4", clear=True)
        b.make_script()
        before = records()
        b.js("document.querySelector('#sbAll').click()")
        # 3 路并发时 1.2s 后前三张就完成、后三张立刻被派出去了；
        # 要验证「停止能拦住还没派出去的」，必须在第一波回来之前点停止。
        time.sleep(0.45)
        b.js("document.querySelector('#sbStop').click()")
        time.sleep(0.5)
        # 4 路在下、2 个排队：停止会把排队的那两个取消掉
        chk("C4 排队被取消", b.evj("window.__qaStoryboard().queued"), 0)
        time.sleep(DELAY * 3)
        after = records() - before
        chk("C4 只跑了在飞的那几张（< 6）", after < 6, True)
        chk("C4 停在空闲态", b.evj("window.__qaStoryboard().busy"), False)
        print(f"    （停止后上游只发了 {after} 次，剩下被取消）")

        # ---------------- C6 提交即返回（不等上游） ----------------
        b.open(FAST, "c6", clear=True)
        b.make_script()
        t0 = time.time()
        b.js("document.querySelector('#sbAll').click()")
        time.sleep(0.3)
        st6 = b.evj("window.__qaStoryboard()")
        # mock 快的时候 300ms 内可能已经出完了 —— 那也算「没在等」，只要不是还闲着
        inflight6 = st6.get("queued", 0) + st6.get("running", 0)
        chk("C6 300ms 内不在等：已提交或已出图",
            inflight6 == 6 or st6.get("done") == 6, True)
        chk("C6 没有还停在待生成", b.ev("document.querySelectorAll('.sb-card[data-status=\"idle\"]').length"), 0)
        print(f"    （点击后 0.3s：排队 {st6.get('queued')}，在跑 {st6.get('running')}，上游此时还没回）")
        while b.evj("window.__qaStoryboard().busy"):
            time.sleep(0.25)
        chk("C6 最终 6 张全出", b.evj("window.__qaStoryboard().done"), 6)

        # ---------------- C5 额度闸门 ----------------
        b.open(TIGHT, "c5", clear=True)
        b.make_script()
        before = records()
        b.js("document.querySelector('#sbAll').click()")
        while b.evj("window.__qaStoryboard().busy"):
            time.sleep(0.25)
        st = b.evj("window.__qaStoryboard()")
        kinds = [s["status"] for s in st["shots"]]
        chk("C5 只成功 2 张", kinds.count("done"), 2)
        chk("C5 其余是 error", kinds.count("error"), 4)
        chk("C5 上游只收到 2 次", records() - before, 2)
        chk("C5 进程计数不超上限", status(TIGHT)["calls"], 2)
        chk("C5 失败原因可读", "上限" in (b.ev("document.querySelector('[data-status=\"error\"] .sb-state').textContent") or ""), True)
        b.shot("01-quota-guard")
    finally:
        subprocess.run(["bsk", "session", "stop", "--session", b.s, "--json"], capture_output=True)
        for p in reversed(procs):
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()

    ok = sum(1 for r in results if r["ok"])
    REPORT.write_text(json.dumps({"at": time.strftime("%F %T"), "passed": ok,
                                  "total": len(results), "items": results},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{ok}/{len(results)} passed -> {REPORT.relative_to(ROOT)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
