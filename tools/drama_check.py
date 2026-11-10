#!/usr/bin/env python3
"""短剧工作室复刻的端到端自检（bsk 驱动，纯前端，无任何生成请求）。

D1 视图切换：?view=drama 直达 / 侧栏点击 / 回创作页
D2 页面结构：4 tab、12 创意标签、8 亮点卡、2 示例项目
D3 输入计数与发送启用
D4 立即生成：剧名、集数、剧本来源标注（模型/模板按实际）、脚注
D5 浮层：风格改标签与状态、集数 6 集重生成
D6 上传 .txt 读入并切回写作页
D7 1440 / 768 / 390 无横向溢出
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = os.environ.get("DRAMA_BASE", "http://127.0.0.1:8788")   # 零计费可指向 mock 代理
BROWSER = "90d43dd3"
QA = Path(__file__).resolve().parent.parent / "qa"
SHOTS = QA / "shots" / "drama"
SHOTS.mkdir(parents=True, exist_ok=True)


def sh(*args, timeout=120):
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
                 "--width", "1500", "--height", "1000", "--no-focus", "--name", "drama-check"))
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
        for _ in range(6):
            v = self.act("evaluate", expr).get("value")
            if v not in (None, ""):
                return v
            time.sleep(0.25)
        return None

    def open(self, tag, view="drama"):
        url = f"{BASE}/?theme=dark&case={tag}" + (f"&view={view}" if view else "")
        d = jl(sh("navigate", "--session", self.s, "--tab-id", str(self.tab), url, "--json", timeout=60))
        if d.get("reached") != "load":
            self.act("navigate", url, tries=3)
        for _ in range(40):
            v = self.ev("JSON.stringify([typeof window.__qaDrama, document.readyState])")
            if v and json.loads(v) == ["function", "complete"]:
                time.sleep(0.4)
                return
            time.sleep(0.25)
        raise RuntimeError(f"页面未就绪: {tag}")

    def shot(self, name):
        self.act("screenshot", "--out", str(SHOTS / f"{name}.png"))


def guard_no_live(base: str, need_llm_off: bool = False) -> None:
    """回归只该打在 mock 上：打真实网关一次误跑就是真金白银，必须在门口拦住。"""
    import urllib.request as _u
    try:
        op = _u.build_opener(_u.ProxyHandler({}))
        with op.open(base + "/api/status", timeout=5) as r:
            st = json.loads(r.read().decode())
    except Exception as e:
        print(f"拒绝启动：{base} 不可达（{e}）", file=sys.stderr)
        raise SystemExit(2)
    if st.get("upstreamMode") == "live" and os.environ.get("ALLOW_LIVE") != "1":
        print(f"拒绝启动：{base} 指向真实网关（upstreamMode=live）。\n"
              "  · 零计费回归请把 BASE 指向 mock 实例\n"
              "  · 确实要跑真实网关，显式设 ALLOW_LIVE=1", file=sys.stderr)
        raise SystemExit(2)
    if need_llm_off and st.get("llm", {}).get("configured") and os.environ.get("ALLOW_LIVE") != "1":
        print(f"拒绝启动：{base} 的文本模型开着，模板断言会变成模型输出。\n"
              "  · 用 JIMENG_LLM=off 启动实例，或设 ALLOW_LIVE=1 显式放行", file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    guard_no_live(BASE, need_llm_off=True)
    b = B()
    results = []

    def chk(name, got, want):
        ok = got == want
        results.append({"name": name, "got": got, "want": want, "ok": ok})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {got!r} (期望 {want!r})")

    try:
        b.tab = jl(sh("tab", "create", "--session", b.s, "--url",
                      f"{BASE}/?theme=dark&view=drama&case=drama0", "--json")).get("tab_id")
        if not b.tab:
            return 2
        b.open("d1")

        # D1 视图
        st = json.loads(b.ev("JSON.stringify(window.__qaDrama())"))
        chk("D1 直达 drama 视图", st["view"], "drama")
        chk("D1 生成页已隐藏", b.ev("getComputedStyle(document.querySelector('#stage')).display"), "none")
        chk("D1 面包屑", b.ev("document.querySelector('#crumb').textContent"), "创作 / 短剧工作室")

        # D2 结构
        chk("D2 tab 数", b.ev("document.querySelectorAll('.drama-tab').length"), 4)
        chk("D2 创意标签", st["ideas"], 12)
        chk("D2 亮点卡", st["feats"], 8)
        chk("D2 示例项目", st["projects"], 2)
        chk("D2 默认选中剧本创作", b.ev("document.querySelector('.drama-tab.is-active').dataset.dtab"), "write")
        b.shot("01-desktop-empty")

        # D3 输入
        b.act("fill", "--selector", "#dramaPrompt",
              "--value", "帮我写一部先婚后爱短剧：女主为拿回家族公司嫁给死对头，婚后才发现他一直在暗中保护她。")
        chk("D3 字数统计", b.ev("document.querySelector('#dramaCount').textContent").endswith("/500 字"), True)
        chk("D3 发送已启用", b.ev("!document.querySelector('#dramaSend').disabled"), True)

        # D4 生成
        b.ev("document.querySelector('#dramaSend').click(), 1")
        time.sleep(0.8)
        out = json.loads(b.ev("""JSON.stringify((() => {
          const o = document.querySelector('#dramaOut');
          return { hidden: o.hidden,
                   badge: (o.querySelector('.drama-badge-local') || {}).textContent || '',
                   badgeTitle: (o.querySelector('.drama-badge-local') || {}).title || '',
                   sb: o.querySelectorAll('.sb-card').length,
                   title: (o.querySelector('h3') || {}).textContent || '',
                   eps: o.querySelectorAll('.script-ep').length,
                   note: [...o.querySelectorAll('.cfg-note')].map(x => x.textContent).join(' '),
                   meta: (o.querySelector('.drama-out-head p') || {}).textContent || '',
                   llm: !!window.__qaStoryboard?.().llm?.configured };
        })())"""))
        chk("D4 结果可见", out["hidden"], False)
        if out["llm"]:
            chk("D4 剧本标注为模型", out["badge"].startswith("剧本：") and "本地模板" not in out["badge"], True)
            chk("D4 标注写明模型与耗时", "生成（" in out["badgeTitle"], True)
        else:
            chk("D4 剧本标注为模板", out["badge"], "剧本：本地模板")
            chk("D4 标注写明未调文本模型", "未调用文本模型" in out["badgeTitle"], True)
        chk("D4 分镜表 6 镜", out["sb"], 6)
        chk("D4 剧名是书名号", out["title"].startswith("《") and len(out["title"]) > 4, True)
        chk("D4 集数与请求一致", out["eps"], 10)
        if not out["llm"]:
            chk("D4 剧名带选题", out["title"].startswith("《先婚后爱："), True)
            chk("D4 明确剧本不是模型输出", "未接文本模型" in out["note"], True)
        chk("D4 脚注说明分镜图是真调模型", "flux-3-image" in out["note"], True)
        chk("D4 元信息", out["meta"], "都市 · 先婚后爱 · 10 集 · 9:16")
        b.shot("02-script")

        # D5 浮层
        b.ev("document.querySelector('#dramaStyle').click(), 1")
        time.sleep(0.45)
        raw = b.ev("[...document.querySelectorAll('.pop-opt')].map(x => x.textContent.trim())")
        opts = raw if isinstance(raw, list) else json.loads(raw)   # bsk 对数组会直接返回 list
        chk("D5 风格选项", opts, ["都市", "古装", "悬疑", "科幻", "家庭"])
        b.ev("(() => { const b=[...document.querySelectorAll('.pop-opt')].find(x=>x.textContent.trim()==='古装'); b && b.click(); return 1; })()")
        time.sleep(0.3)
        chk("D5 风格生效", b.ev("window.__qaDrama().style"), "古装")
        chk("D5 按钮保留字段名", b.ev("document.querySelector('#dramaStyle').textContent.includes('风格')"), True)
        chk("D5 值显示在按钮上", b.ev("document.querySelector('#dramaStyleVal').textContent"), "古装")

        b.ev("document.querySelector('#dramaEps').click(), 1")
        time.sleep(0.4)
        b.ev("(() => { const b=[...document.querySelectorAll('.pop-opt')].find(x=>x.textContent.trim()==='6 集'); b && b.click(); return 1; })()")
        time.sleep(0.3)
        b.ev("document.querySelector('#dramaSend').click(), 1")
        time.sleep(0.6)
        chk("D5 改集数后重生成", b.ev("document.querySelectorAll('.script-ep').length"), 6)
        chk("D5 元信息跟随", b.ev("document.querySelector('.drama-out-head p').textContent"), "古装 · 先婚后爱 · 6 集 · 9:16")
        b.shot("03-popover-result")

        # D6 上传
        b.ev("""(async () => {
          const f = new File(['第一集：她在婚礼上认出司仪是失踪七年的哥哥。'], 'outline.txt', { type: 'text/plain' });
          const dt = new DataTransfer(); dt.items.add(f);
          const input = document.querySelector('#dramaFile');
          input.files = dt.files;
          input.dispatchEvent(new Event('change', { bubbles: true }));
          return 1;
        })()""")
        time.sleep(0.9)
        up = json.loads(b.ev("""JSON.stringify({
          len: document.querySelector('#dramaPrompt').value.length,
          head: document.querySelector('#dramaPrompt').value.slice(0, 8),
          tab: window.__qaDrama().tab })"""))
        chk("D6 读入文本", (up["len"] > 10, up["head"]), (True, "第一集：她在婚礼"))
        chk("D6 自动切回写作页", up["tab"], "write")

        # D7 三档尺寸
        for w, h, name in ((1440, 900, "04-desktop-1440"), (768, 1024, "05-tablet-768"), (390, 844, "06-mobile-390")):
            b.act("emulate", "--width", str(w), "--height", str(h),
                  "--dpr", "2" if w <= 900 else "1", *(["--mobile"] if w <= 640 else []))
            time.sleep(0.7)
            m = json.loads(b.ev("JSON.stringify({vw: innerWidth, docW: document.documentElement.scrollWidth})"))
            chk(f"D7 {w} 视口", m["vw"], w)
            chk(f"D7 {w} 无横向溢出", m["docW"] <= w, True)
            b.shot(name)

        # D1 回创作页
        b.ev("(() => { const b=[...document.querySelectorAll('.nav-item')].find(x=>x.dataset.nav==='创作'); b && b.click(); return 1; })()")
        time.sleep(0.6)
        chk("D1 切回创作页", b.ev("window.__qaDrama().view"), "generate")
        chk("D1 生成页恢复", b.ev("getComputedStyle(document.querySelector('#stage')).display") != "none", True)

        rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "base": BASE,
               "passed": sum(1 for r in results if r["ok"]), "total": len(results),
               "checks": results}
        (QA / "drama-report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
        print(f"\n{rep['passed']}/{rep['total']} passed -> qa/drama-report.json")
        return 0 if rep["passed"] == rep["total"] else 1
    finally:
        sh("session", "stop", b.s, "--json", timeout=60)


if __name__ == "__main__":
    sys.exit(main())
