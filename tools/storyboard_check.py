#!/usr/bin/env python3
"""短剧「分镜」接线自检：镜头文字是模板，画面必须是 flux-3-image 真出的。

为什么单独一个工具
------------------
分镜的失败模式不是「页面崩了」，而是**用占位图冒充模型产物**。所以这里逐条证伪：

  S1 结构      立即生成后出现 6 张分镜卡，镜号/景别/时长/台词齐全
  S2 提示词    每张卡的实际请求提示词含景别与「不出现任何文字」，长度合理
  S3 演示守卫  demo 模式下点生成 → 状态仍 idle，上游调用数不变（拿不出真图就不出图）
  S4 单张真出  mock 上游：S01 → done，img 已解码 1024×1024，徽标改为 MOCK · 1/6
  S5 批量      生成全部 → 6/6 done，代理 calls 恰好 +5（不放大请求）
  S6 失败透明  上游配额用尽（maxCalls=0 实例）→ error 卡片 + 「重试」，不产出图片
  S7 版式      1440/768/390 无横向溢出，网格 6/4/2 列
  S8 主题      深/浅色各一张截图

默认全部跑在 mock 实例上（零计费）。`--real` 才走 8788 真实网关。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
SHOTS = QA / "shots" / "storyboard"
SHOTS.mkdir(parents=True, exist_ok=True)
REPORT = QA / "storyboard-report.json"
UPSTREAM = QA / "mock-upstream.jsonl"

BROWSER = "90d43dd3"
MOCK = "http://127.0.0.1:8790"     # 指向 127.0.0.1:8799 的 mock 代理
DEAD = "http://127.0.0.1:8793"     # 配额 0 的代理：用来造「上游拒绝」（8790-8792 是 config_test 占用）
LIVE = "http://127.0.0.1:8788"
PROMPT = ("帮我写一部先婚后爱短剧：女主为拿回家族公司嫁给死对头，"
          "婚后才发现他一直在暗中保护她。")


def sh(*args, timeout=120) -> str:
    r = subprocess.run(["bsk", *args], capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip()


def jl(raw: str) -> dict:
    try:
        return json.loads(raw, strict=False)
    except Exception:
        return {"__raw": raw[:300]}


def calls_of(base: str) -> int:
    try:
        with urllib.request.urlopen(f"{base}/api/status", timeout=5) as r:
            return int(json.loads(r.read())["calls"])
    except Exception:
        return -1


class B:
    def __init__(self):
        d = jl(sh("session", "start", "--json", "--browser", BROWSER,
                 "--width", "1500", "--height", "1000", "--no-focus", "--name", "storyboard-check"))
        self.s = d.get("session_id")
        if not self.s:
            raise RuntimeError(f"session start 失败: {d}")
        self.tab = jl(sh("tab", "create", "--session", self.s, "--url", "about:blank", "--json")).get("tab_id")
        if not self.tab:
            raise RuntimeError("tab create 失败")

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

    def ev(self, expr, tries=6):
        for _ in range(tries):
            v = self.act("evaluate", expr).get("value")
            if v not in (None, ""):
                return v
            time.sleep(0.2)
        return None

    def js(self, expr):
        """只发一次 evaluate。点击类表达式没有返回值，用 ev() 会被重试成多次点击。"""
        return self.act("evaluate", expr).get("value")

    def evj(self, expr):
        raw = self.ev(f"JSON.stringify({expr})")
        return json.loads(raw) if raw else None

    def wait_ready(self):
        for _ in range(60):
            v = self.ev("JSON.stringify([typeof window.__qaStoryboard, document.readyState, "
                        "window.__qaSnapshot && window.__qaSnapshot().statusReady])")
            if v:
                f, ready, sready = json.loads(v)
                if f == "function" and ready == "complete" and sready:
                    time.sleep(0.3)
                    return True
            time.sleep(0.25)
        return False

    def open(self, base, tag, view="drama", theme="dark", clear=False):
        url = f"{base}/?theme={theme}&case={tag}" + (f"&view={view}" if view else "")
        for attempt in range(3):
            d = jl(sh("navigate", "--session", self.s, "--tab-id", str(self.tab), url, "--json", timeout=60))
            if d.get("reached") == "load":
                break
            self.act("navigate", url)
        if not self.wait_ready():
            raise RuntimeError(f"页面未就绪: {tag} {self.ev('location.href')}")
        if clear:
            self.js("localStorage.removeItem('jimeng-storyboard-v1'), 1")
            d = jl(sh("navigate", "--session", self.s, "--tab-id", str(self.tab),
                      f"{url}&r=2", "--json", timeout=60))
            if d.get("reached") != "load":
                self.act("navigate", f"{url}&r=2")
            if not self.wait_ready():
                raise RuntimeError(f"清档后未就绪: {tag}")

    def shot(self, name):
        self.act("screenshot", "--out", str(SHOTS / f"{name}.png"))

    def make_script(self):
        # 被测算例服务必须 JIMENG_LLM=off：否则点发送会真调文本模型，
        # 镜头变成模型写的，模板断言全崩（也会白花钱）
        if self.ev("!!window.__qaStoryboard?.().llm?.configured"):
            raise RuntimeError("被测实例开着文本模型：请用 JIMENG_LLM=off 启动后再跑本用例")
        self.act("fill", "--selector", "#dramaPrompt", "--value", PROMPT)
        self.js("document.querySelector('#dramaSend').click()")
        for _ in range(40):
            if self.ev("document.querySelectorAll('.sb-card').length") == 6:
                time.sleep(0.25)
                return True
            time.sleep(0.2)
        return False

    def wait_status(self, sid, want="done", timeout=90):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.evj(f"(window.__qaStoryboard().shots.find(s=>s.id==='{sid}')||{{}}).status")
            if st == want:
                return True
            time.sleep(0.4)
        return False

    def wait_idle(self, timeout=180):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.ev("window.__qaStoryboard().busy === false"):
                return True
            time.sleep(0.5)
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="走 8788 真实网关（会计费）")
    ap.add_argument("--real-all", action="store_true", help="真实网关 + 一次跑满 6 镜")
    args = ap.parse_args()

    b = B()
    results = []

    def chk(name, got, want):
        ok = got == want
        results.append({"name": name, "got": got, "want": want, "ok": ok})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {got!r} (期望 {want!r})")

    try:
        # ---------------- 结构与守卫（mock 实例，零计费） ----------------
        b.open(MOCK, "s1")
        chk("S1 立即生成出 6 张分镜卡", b.make_script(), True)
        st = b.evj("window.__qaStoryboard()")
        chk("S1 初始全部 idle", [s["status"] for s in st["shots"]], ["idle"] * 6)
        chk("S1 徽标显示 0/6", "0/6" in st["badge"], True)
        chk("S1 镜号连续", [s["id"] for s in st["shots"]],
            ["S01", "S02", "S03", "S04", "S05", "S06"])
        chk("S1 景别齐全", [s["size"] for s in st["shots"]],
            ["大全景", "中景", "特写", "近景", "过肩", "俯拍"])
        chk("S1 卡上有台词行", b.ev("document.querySelectorAll('.sb-card .sb-line').length"), 6)

        prompts = st["prompts"]
        chk("S2 每张独立提示词", len(set(prompts)), 6)
        chk("S2 提示词含景别", all(s["size"] in p for s, p in zip(st["shots"], prompts)), True)
        chk("S2 提示词禁文字", all("不出现任何文字" in p for p in prompts), True)
        chk("S2 提示词长度合理", all(40 <= len(p) <= 220 for p in prompts), True)
        b.shot("01-idle-desktop")

        # ---------------- 演示模式：拿不出真图就不出图 ----------------
        before = calls_of(MOCK)
        b.ev("window.__qaApply({demo:true})")
        b.js("document.querySelector('[data-shot=\"S01\"] .sb-gen').click()")
        time.sleep(1.0)
        st = b.evj("window.__qaStoryboard()")
        chk("S3 演示模式不出图", st["shots"][0]["status"], "idle")
        chk("S3 徽标改为演示", st["badgeMode"], "demo")
        chk("S3 上游调用数不变", calls_of(MOCK) - before, 0)
        b.ev("window.__qaApply({demo:false})")

        # ---------------- 单张真出（mock 上游） ----------------
        before = calls_of(MOCK)
        b.js("document.querySelector('[data-shot=\"S01\"] .sb-gen').click()")
        chk("S4 S01 出图", b.wait_status("S01", "done", 60), True)
        st = b.evj("window.__qaStoryboard()")
        s1 = st["shots"][0]
        chk("S4 产物路径来自代理", str(s1["img"]).startswith("/assets/generated/"), True)
        chk("S4 标注模型与耗时", (s1["model"] or "").endswith("flux-3-image") and s1["ms"] > 0, True)
        chk("S4 徽标 1/6", "1/6" in st["badge"], True)
        chk("S4 代理 calls +1", calls_of(MOCK) - before, 1)
        dec = b.evj("(()=>{const i=document.querySelector('[data-shot=\"S01\"] .sb-img');"
                    "return {w:i.naturalWidth,h:i.naturalHeight,ok:i.complete&&i.naturalWidth>0};})()")
        chk("S4 图片真实解码", dec["ok"] and dec["w"] > 0, True)
        b.shot("02-one-done")

        # ---------------- 批量：不放大请求 ----------------
        before = calls_of(MOCK)
        b.js("document.querySelector('#sbAll').click()")
        chk("S5 批量跑完", b.wait_idle(120), True)
        st = b.evj("window.__qaStoryboard()")
        chk("S5 6/6 全部出图", st["done"], 6)
        chk("S5 代理 calls 恰好 +5", calls_of(MOCK) - before, 5)
        chk("S5 徽标 6/6", "6/6" in st["badge"], True)
        chk("S5 全部按钮转为已生成", b.ev("document.querySelector('#sbAll').textContent"), "本集已全部生成")
        b.shot("03-all-done")

        # ---------------- 多集 ----------------
        chk("S11 第 1 集上一集禁用", b.ev("document.querySelector('#sbPrev').disabled"), True)
        ep1_desc = b.evj("window.__qaStoryboard().shots[0].desc")
        ep1_prop = b.evj("window.__qaStoryboard().shots[2].desc")
        ep1_prompt = b.evj("window.__qaStoryboard().prompts[0]")
        b.js("document.querySelector('#sbNext').click()")
        time.sleep(0.45)
        st2 = b.evj("window.__qaStoryboard()")
        chk("S11 切到第 2 集", st2["ep"], 2)
        chk("S11 第 2 集是新的 6 镜", [st2["total"], st2["done"]], [6, 0])
        chk("S11 第 2 集场景变了", st2["shots"][0]["desc"] != ep1_desc, True)
        chk("S11 第 2 集道具换了", st2["shots"][2]["desc"] != ep1_prop, True)
        chk("S11 徽标回到 0/6", "0/6" in st2["badge"], True)
        chk("S11 集数显示 2 / 10", b.ev("document.querySelector('#sbEpNow').textContent"), "2 / 10")
        # 第 2 集独立出图：提示词来自本集场景，进度互不影响
        p2 = b.evj("window.__qaStoryboard().prompts[0]")
        chk("S11 第 2 集提示词与第 1 集不同", p2 != ep1_prompt, True)
        before = calls_of(MOCK)
        b.js("document.querySelector('[data-shot=\"S01\"] .sb-gen').click()")
        chk("S11 第 2 集可独立出图", b.wait_status("S01", "done", 60), True)
        chk("S11 第 2 集只发 1 次", calls_of(MOCK) - before, 1)
        st = b.evj("window.__qaStoryboard()")
        chk("S11 第 2 集 1/6，全剧 7/60", [st["ep"], st["done"], st["boardDone"], st["boardTotal"]],
            [2, 1, 7, 60])
        b.js("document.querySelector('#sbPrev').click()")
        time.sleep(0.45)
        st1 = b.evj("window.__qaStoryboard()")
        chk("S11 回第 1 集仍 6/6 且全剧 7", [st1["ep"], st1["done"], st1["boardDone"]], [1, 6, 7])

        # ---------------- 改镜：改描述 + 换景别 ----------------
        b.js("document.querySelector('[data-shot=\"S02\"] .sb-edit').click()")
        time.sleep(0.45)
        chk("S12 打开改镜浮层", b.ev("!!document.querySelector('#sbEditDesc')"), True)
        b.js("(()=>{const t=document.querySelector('#sbEditDesc');"
             "t.value='深夜加油站的便利店门口，两人隔着加油机对视，霓虹在积水上反光';return 1})()")
        b.js("[...document.querySelectorAll('.sb-size')].find(x=>x.textContent==='特写').click()")
        time.sleep(0.2)
        b.js("document.querySelector('#sbEditSave').click()")
        time.sleep(0.4)
        st = b.evj("window.__qaStoryboard()")
        chk("S12 描述已改", "加油站" in st["shots"][1]["desc"], True)
        chk("S12 景别已改", st["shots"][1]["size"], "特写")
        chk("S12 标记待重出", st["shots"][1]["stale"], True)
        chk("S12 原图没丢", st["shots"][1]["img"] is not None, True)
        chk("S12 按钮变成按新提示词", b.ev("document.querySelector('[data-shot=\"S02\"] .sb-gen').textContent"),
            "按新提示词生成")
        chk("S12 提示词带上新内容",
            "加油站" in st["prompts"][1] and "特写" in st["prompts"][1], True)
        before = calls_of(MOCK)
        b.js("document.querySelector('[data-shot=\"S02\"] .sb-gen').click()")
        chk("S12 重出完成", b.wait_status("S02", "done", 60), True)
        chk("S12 只发 1 次请求", calls_of(MOCK) - before, 1)
        st = b.evj("window.__qaStoryboard()")
        chk("S12 重出后 stale 清除", st["shots"][1]["stale"], False)
        last = json.loads(UPSTREAM.read_text(encoding="utf-8").strip().splitlines()[-1])
        chk("S12 上游真收到新提示词", "加油站" in json.dumps(last, ensure_ascii=False), True)
        b.js("document.querySelector('#sbSection').scrollIntoView({block:'start'})")
        time.sleep(0.4)
        b.shot("10-edited-and-multi-ep")

        # ---------------- 点图看大图 ----------------
        b.js("document.querySelector('[data-shot=\"S01\"] .sb-img').click()")
        time.sleep(0.4)
        chk("S10 点图开灯箱", b.ev("document.querySelector('#lightbox').hidden"), False)
        chk("S10 灯箱标注来源不冒充",
            "MOCK 上游 · 零计费" in (b.ev("document.querySelector('#lightboxMeta').textContent") or ""), True)
        b.js("document.querySelector('#lightboxClose').click()")
        time.sleep(0.3)
        chk("S10 可关闭", b.ev("document.querySelector('#lightbox').hidden"), True)

        # ---------------- 刷新不丢 ----------------
        before_board = b.evj("window.__qaStoryboard()")
        b.open(MOCK, "s13-reload")
        st = b.evj("window.__qaStoryboard()")
        chk("S13 刷新后分镜仍在", [st["total"], st["ep"], st["totalEp"]],
            [before_board["total"], before_board["ep"], before_board["totalEp"]])
        chk("S13 刷新后仍是 6/6", st["done"], 6)
        chk("S13 改过的描述仍在", "加油站" in st["shots"][1]["desc"], True)
        chk("S13 图片仍能解码", b.evj("(()=>{const i=document.querySelector('.sb-card[data-status=\"done\"] .sb-img');"
                                    "return !!i && i.complete && i.naturalWidth>0})()"), True)
        chk("S13 全剧进度保留", st["progress"], f'全剧 {st["boardDone"]}/{st["boardTotal"]} 镜已出图')
        chk("S13 全剧总数=集数×6", st["boardTotal"], st["totalEp"] * 6)
        b.js("document.querySelector('#sbSection').scrollIntoView({block:'start'})")
        time.sleep(0.4)
        b.shot("11-restored-after-reload")

        # ---------------- 版式 ----------------
        for w, h, name, cols, mobile in ((1440, 900, "04-desktop-1440", 6, False),
                                         (768, 900, "05-tablet-768", 3, False),
                                         (390, 844, "06-mobile-390", 1, True)):
            emu = ["--width", str(w), "--height", str(h), "--dpr", "2"]
            if mobile:
                emu.append("--mobile")
            b.act("emulate", *emu)
            time.sleep(0.6)
            b.open(MOCK, f"s7-{w}", clear=True)
            chk(f"S7 {w} 视口生效", b.ev("innerWidth"), w)
            b.make_script()
            ov = b.ev("document.documentElement.scrollWidth > innerWidth + 1")
            chk(f"S7 {w} 无横向溢出", ov, False)
            chk(f"S7 {w} 网格列数", b.ev("getComputedStyle(document.querySelector('#sbGrid'))"
                                         ".gridTemplateColumns.split(' ').length"), cols)
            b.js("document.querySelector('#sbSection').scrollIntoView({block:'start'})")
            time.sleep(0.4)
            b.shot(name)
        b.act("emulate", "--width", "1440", "--height", "900", "--dpr", "2")

        # 浅色
        b.open(MOCK, "s8-light", theme="light", clear=True)
        b.make_script()
        b.js("document.querySelector('#sbSection').scrollIntoView({block:'start'})")
        time.sleep(0.4)
        b.shot("07-desktop-light")
        chk("S8 浅色无溢出", b.ev("document.documentElement.scrollWidth > innerWidth + 1"), False)

        # ---------------- 失败透明（配额 0 实例） ----------------
        b.open(DEAD, "s6-error", clear=True)
        b.make_script()
        b.js("document.querySelector('[data-shot=\"S02\"] .sb-gen').click()")
        chk("S6 上游拒绝进 error", b.wait_status("S02", "error", 60), True)
        st = b.evj("window.__qaStoryboard()")
        s2 = st["shots"][1]
        chk("S6 不产出图片", s2["img"], None)
        chk("S6 错误可见", bool(s2["error"]), True)
        chk("S6 按钮变重试", b.ev("document.querySelector('[data-shot=\"S02\"] .sb-gen').textContent"), "重试")
        chk("S6 卡片进入 error 态",
            b.ev("document.querySelector('[data-shot=\"S02\"]').dataset.status"), "error")
        b.shot("08-error")

        # ---------------- 真实网关 ----------------
        if args.real or args.real_all:
            b.act("emulate", "--width", "1440", "--height", "900", "--dpr", "2")
            b.open(LIVE, "s9-live", clear=True)
            chk("S9 真实链路就绪", b.ev("window.__qaStoryboard().live"), True)
            chk("S9 真实链路非 mock", b.ev("window.__qaStoryboard().upstreamMode"), "live")
            b.make_script()
            before = calls_of(LIVE)
            if args.real_all:
                b.js("document.querySelector('#sbAll').click()")
                time.sleep(1.6)
                b.js("document.querySelector('#sbSection').scrollIntoView({block:'start'})")
                time.sleep(0.5)
                b.shot("09-live-generating")
                lanes = b.ev("document.querySelectorAll('.sb-card[data-status=\"running\"] .sb-bar:not([hidden])').length")
                chk("S9 生成中有进度条（并发下可多张）", 1 <= lanes <= 4, True)
                chk("S9 真实批量跑完", b.wait_idle(360), True)
            else:
                b.js("document.querySelector('[data-shot=\"S01\"] .sb-gen').click()")
                chk("S9 S01 真出图", b.wait_status("S01", "done", 180), True)
            st = b.evj("window.__qaStoryboard()")
            chk("S9 真实链路 calls 增量",
                calls_of(LIVE) - before, 6 if args.real_all else 1)
            chk("S9 全部 done", st["done"], 6 if args.real_all else 1)
            chk("S9 图片解码 1024", b.evj("(()=>{const i=document.querySelector('.sb-card[data-status=\"done\"] .sb-img');"
                                        "return i? [i.naturalWidth,i.naturalHeight]:null;})()"), [1024, 1024])
            b.shot("09-live-real")
            results.append({"name": "S9 真实产物", "got": [s["img"] for s in st["shots"] if s["img"]],
                            "want": "见 qa/storyboard-report.json", "ok": True})
    finally:
        try:
            sh("session", "stop", "--session", b.s, "--json", timeout=30)
        except Exception:
            pass

    ok = sum(1 for r in results if r["ok"])
    REPORT.write_text(json.dumps({"at": time.strftime("%F %T"), "real": args.real or args.real_all,
                                  "passed": ok, "total": len(results), "items": results},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{ok}/{len(results)} passed -> {REPORT.relative_to(ROOT)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
