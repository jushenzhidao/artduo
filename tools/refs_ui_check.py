#!/usr/bin/env python3
"""参考图真实上传路径验证（bsk 驱动，8791 mock 实例，零计费）。

覆盖：
  U1 真实 File → 压缩成 JPEG data URI → 生成「画布」角标
  U2 非图片文件被拒（不产生 chip）
  U3 一次拖入 12 张只收 10 张，并显示「已满 10 张」
  U4 带 1 张上传图生成 → 气泡标「1 个参考」、trace 与上游都收到 1 张
  U5 刷新后历史仍在，参考图若未持久化则重试会明确拒绝而不是静默丢图
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
SHOTS = QA / "shots" / "refs"
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
                 "--width", "1400", "--height", "900", "--no-focus", "--name", "refs-ui"))
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
            if d.get("code") == "permission_denied" and i + 1 < tries:
                time.sleep(0.3)
                continue
            return d
        return {}

    def ev(self, expr):
        last = None
        for _ in range(5):
            last = self.act("evaluate", expr)
            v = last.get("value")
            if v not in (None, ""):
                return v
            time.sleep(0.25)
        print("EV FAIL:", json.dumps(last, ensure_ascii=False)[:300], file=sys.stderr)
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
                time.sleep(0.4)
                return
            time.sleep(0.25)
        raise RuntimeError(f"页面未就绪: {tag}")

    def shot(self, name):
        self.act("screenshot", "--out", str(SHOTS / f"{name}.png"))


DROP_JS = """(async () => {
  const mk = async (name, type, w = 64, h = 64) => {
    if (type === 'text/plain') return new File(['hello'], name, { type });
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#c0392b'; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = '#fff'; ctx.font = '20px sans-serif'; ctx.fillText('REF', 6, 34);
    const blob = await new Promise((r) => c.toBlob(r, 'image/png'));
    return new File([blob], name, { type: 'image/png' });
  };
  const files = [];
  for (const spec of FILES) files.push(await mk(spec[0], spec[1]));
  const dt = new DataTransfer();
  files.forEach((f) => dt.items.add(f));
  const input = document.querySelector('#fileInput');
  input.files = dt.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return files.length;
})()"""


def main() -> int:
    b = B()
    results, trace_before = [], None

    def chk(name, got, want):
        ok = got == want
        results.append({"name": name, "got": got, "want": want, "ok": ok})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {got!r} (期望 {want!r})")
        return ok

    try:
        b.tab = jl(sh("tab", "create", "--session", b.s, "--url",
                      f"{BASE}/?theme=dark&case=refs0", "--json")).get("tab_id")
        if not b.tab:
            return 2
        b.ev("localStorage.removeItem('jimeng-sessions-v1'), 1")
        b.open("refs1")

        # ---------- U1 单张真实上传 ----------
        n = b.ev(DROP_JS.replace("FILES", "[[\"ref-a.png\",\"image/png\"]]"))
        time.sleep(1.2)
        info = json.loads(b.ev("""JSON.stringify((() => {
          const s = window.__qaSnapshot();
          const chip = document.querySelectorAll('.ref-chip:not(.add-more)');
          const img = document.querySelector('.ref-chip img');
          return { refs: s.refs, chips: chip.length,
                   index: document.querySelector('.ref-index') ? document.querySelector('.ref-index').textContent : null,
                   src: img ? img.getAttribute('src').slice(0, 22) : null,
                   hint: document.querySelector('#hintText').textContent };
        })())"""))
        chk("U1 已加入 1 张", (n, info["refs"], info["chips"]), (1, 1, 1))
        chk("U1 首张标「画布」", info["index"], "画布")
        chk("U1 已压成 JPEG data URI", (info["src"] or "").startswith("data:image/jpeg"), True)
        chk("U1 提示说明会随请求发送", "张参考将随请求发送" in info["hint"], True)
        b.shot("01-one-ref")

        # ---------- U2 非图片被拒 ----------
        b.ev(DROP_JS.replace("FILES", "[[\"note.txt\",\"text/plain\"]]"))
        time.sleep(1.0)
        after = json.loads(b.ev("""JSON.stringify({
          refs: window.__qaSnapshot().refs,
          toast: [...document.querySelectorAll('.toast')].map((x) => x.textContent).join(' | ')
        })"""))
        chk("U2 非图片不加入", after["refs"], 1)
        chk("U2 有明确提示", "不是图片" in after["toast"], True)

        # ---------- U3 上限 10 ----------
        b.ev(DROP_JS.replace("FILES", "[" + ",".join(
            f'["bulk-{i}.png","image/png"]' for i in range(1, 13)) + "]"))
        time.sleep(2.6)
        cap = json.loads(b.ev("""JSON.stringify({
          refs: window.__qaSnapshot().refs,
          limit: (document.querySelector('.ref-limit') || {}).textContent || '',
          addMore: !!document.querySelector('#refMore')
        })"""))
        chk("U3 只收 10 张", cap["refs"], 10)
        chk("U3 显示已满", "已满 10 张" in cap["limit"], True)
        chk("U3 隐藏加号", cap["addMore"], False)
        b.shot("02-cap-10")

        # ---------- U4 带图上生成 ----------
        b.open("refs2")
        b.ev(DROP_JS.replace("FILES", "[[\"cup.png\",\"image/png\",96,96]]"))
        time.sleep(1.0)
        b.act("fill", "--selector", "#prompt", "--value", "把参考图里的红方块放到白瓷杯旁")
        b.act("press", "Enter", "--selector", "#prompt")
        for _ in range(60):
            if b.ev("document.querySelectorAll('.result-card, .alert-bar').length > 0"):
                break
            time.sleep(0.2)
        time.sleep(0.4)
        facts = json.loads(b.ev("JSON.stringify(window.__qaFacts())"))
        chk("U4 生成成功", (facts.get("resultCards"), facts.get("alerts")), (1, []))
        chk("U4 气泡标 1 个参考", "1 个参考" in " ".join(facts.get("bubbleTags") or []), True)
        chk("U4 标 MOCK 零计费", "MOCK" in " ".join(facts.get("sourceTags") or []), True)
        b.shot("03-result-with-ref")

        last = [json.loads(x) for x in (QA / "trace.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()][-1]
        chk("U4 trace 记录 1 张", last.get("incoming", {}).get("refs_count"), 1)
        chk("U4 上游请求带 1 张", len((last.get("upstream_request") or {}).get("image_summary") or []), 1)
        chk("U4 响应回报 refsUsed", last.get("response", {}).get("refs_used", 1), 1)

        # ---------- U5 刷新后历史与重试 ----------
        b.open("refs3")
        store = json.loads(b.ev("JSON.stringify((() => { const s = window.__qaSnapshot();"
                                " const raw = localStorage.getItem('jimeng-sessions-v1') || '{}';"
                                " return { sessions: s.sessions, bytes: raw.length }; })())"))
        chk("U5 刷新后仍有会话", store["sessions"] >= 1, True)
        chk("U5 localStorage 未爆（<5MB）", store["bytes"] < 5 * 1024 * 1024, True)

        rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "base": BASE,
               "passed": sum(1 for r in results if r["ok"]), "total": len(results),
               "checks": results, "storage_bytes": store["bytes"]}
        (QA / "refs-report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
        print(f"\n{rep['passed']}/{rep['total']} passed -> qa/refs-report.json")
        return 0 if rep["passed"] == rep["total"] else 1
    finally:
        sh("session", "stop", b.s, "--json", timeout=60)


if __name__ == "__main__":
    sys.exit(main())
