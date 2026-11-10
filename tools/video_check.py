#!/usr/bin/env python3
"""「分镜图 → 视频」链路自检（bsk 驱动，mock avm，零计费）。

要把「视频真的走了上游」和「页面没在骗人」分开证伪：

  V1 只对已出图的镜头开放入口
  V2 提示词带运镜、且禁止画面出现文字
  V3 提交一次 = 上游 1 次 create（连点不放大）
  V4 请求契约：minimaxH3 / 720p / 8s / adaptive / first_frame / 首帧 9:16 / Bearer
  V5 轮询 → 成片落地成本地 mp4，`<video>` 能解码
  V6 上游 failed → 卡片 error + 按钮变「重试视频」
  V7 成功但转存失败 → 明确报错，不假装有片
  V8 未配置 AVM_COOKIE 的实例 → 按钮禁用并说明原因
  V9 刷新后视频还在（存档）
  V10 390 无横向溢出
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
SHOTS = QA / "shots" / "video"
SHOTS.mkdir(parents=True, exist_ok=True)
RECORD = QA / "mock-avm.jsonl"
REPORT = QA / "video-report.json"

BROWSER = "90d43dd3"
MOCK = "http://127.0.0.1:8796"      # clone 实例 → AVM_BASE 指向 mock-avm
NOAVM = "http://127.0.0.1:8797"     # clone 实例 → 无 AVM_COOKIE
GEN = "http://127.0.0.1:8799"       # 分镜图仍走 mock flux
REF_1PX = ("data:image/png;base64,"
           "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def sh(*args, timeout=120) -> str:
    r = subprocess.run(["bsk", *args], capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip()


def jl(raw: str) -> dict:
    try:
        return json.loads(raw, strict=False)
    except Exception:
        return {"__raw": raw[:300]}


def post_reel(files: list[str], base: str = MOCK):
    import urllib.error
    import urllib.request
    body = json.dumps({"files": files, "ep": 1}).encode()
    req = urllib.request.Request(base + "/api/reel", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def post_video_tier(tier: str, base: str = MOCK):
    import urllib.error
    import urllib.request
    payload = {"prompt": "档位门禁探针", "image": REF_1PX, "tier": tier}
    req = urllib.request.Request(base + "/api/video", data=json.dumps(payload).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def records() -> list[dict]:
    if not RECORD.exists():
        return []
    out = []
    for line in RECORD.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


class B:
    def __init__(self):
        d = jl(sh("session", "start", "--json", "--browser", BROWSER,
                 "--width", "1500", "--height", "1000", "--no-focus", "--name", "video-check"))
        self.s = d.get("session_id")
        if not self.s:
            raise RuntimeError(f"session start 失败: {d}")
        self.tab = jl(sh("tab", "create", "--session", self.s, "--url", "about:blank", "--json")).get("tab_id")

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

    def js(self, expr):
        return self.act("evaluate", expr).get("value")

    def ev(self, expr, tries=6):
        for _ in range(tries):
            v = self.act("evaluate", expr).get("value")
            if v not in (None, ""):
                return v
            time.sleep(0.2)
        return None

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

    def open(self, base, tag, clear=True):
        url = f"{base}/?theme=dark&view=drama&case={tag}"
        d = jl(sh("navigate", "--session", self.s, "--tab-id", str(self.tab), url, "--json", timeout=60))
        if d.get("reached") != "load":
            self.act("navigate", url)
        if not self.wait_ready():
            raise RuntimeError(f"页面未就绪: {tag}")
        if clear:
            self.js("localStorage.removeItem('jimeng-storyboard-v1'), 1")
            d = jl(sh("navigate", "--session", self.s, "--tab-id", str(self.tab), url + "&r=2", "--json", timeout=60))
            if d.get("reached") != "load":
                self.act("navigate", url + "&r=2")
            if not self.wait_ready():
                raise RuntimeError(f"清档后未就绪: {tag}")

    def shot(self, name):
        self.js("document.querySelector('#sbSection')?.scrollIntoView({block:'start'})")
        time.sleep(0.45)
        self.act("screenshot", "--out", str(SHOTS / f"{name}.png"))

    def make_script(self):
        # 被测算例服务必须 JIMENG_LLM=off：否则点发送会真调文本模型，
        # 镜头变成模型写的，模板断言全崩（也会白花钱）
        if self.ev("!!window.__qaStoryboard?.().llm?.configured"):
            raise RuntimeError("被测实例开着文本模型：请用 JIMENG_LLM=off 启动后再跑本用例")
        self.act("fill", "--selector", "#dramaPrompt", "--value",
                 "帮我写一部先婚后爱短剧：女主为拿回家族公司嫁给死对头，婚后才发现他一直在暗中保护她。")
        self.js("document.querySelector('#dramaSend').click()")
        for _ in range(40):
            if self.ev("document.querySelectorAll('.sb-card').length") == 6:
                time.sleep(0.25)
                return True
            time.sleep(0.2)
        return False

    def gen_image(self, sid="S01"):
        self.js(f"document.querySelector('[data-shot=\"{sid}\"] .sb-gen').click()")
        return self.wait_shot(sid, "status", "done", 90)

    def wait_shot(self, sid, field, want, timeout=90):
        t0 = time.time()
        while time.time() - t0 < timeout:
            v = self.evj(f"(window.__qaStoryboard().shots.find(s=>s.id==='{sid}')||{{}}).{field}")
            if v == want:
                return True
            time.sleep(0.4)
        return False

    def click_video(self, sid="S01"):
        self.js(f"document.querySelector('[data-shot=\"{sid}\"] .sb-vid').click()")


def main() -> int:
    b = B()
    results = []

    def chk(name, got, want):
        ok = got == want
        results.append({"name": name, "got": got, "want": want, "ok": ok})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {got!r} (期望 {want!r})")

    try:
        # ---------------- 入口与提示词 ----------------
        b.open(MOCK, "v1")
        chk("V1 分镜表就绪", b.make_script(), True)
        chk("V1 未出图时无视频按钮", b.ev("document.querySelector('[data-shot=\"S01\"] .sb-vid').hidden"), True)
        chk("V1 分镜图出图", b.gen_image("S01"), True)
        chk("V1 出图后按钮出现", b.ev("document.querySelector('[data-shot=\"S01\"] .sb-vid').hidden"), False)
        chk("V1 按钮文案", b.ev("document.querySelector('[data-shot=\"S01\"] .sb-vid').textContent"), "出视频")
        vp = b.evj("window.__qaStoryboard().videoPrompts")
        chk("V2 每条视频提示词都带运镜", all(any(m in p for m in
            ["推入", "跟随", "推近", "下压", "横移", "升起"]) for p in vp), True)
        chk("V2 视频提示词禁止文字", all("不出现任何文字" in p for p in vp), True)
        chk("V2 视频提示词各不相同", len(set(vp)), 6)

        # ---------------- 提交 → 轮询 → 成片 ----------------
        before = len(records())
        b.click_video("S01")
        time.sleep(1.2)
        chk("V3 点击后进入生成态",
            b.evj("window.__qaStoryboard().shots[0].vidStatus") in ("queued", "running"), True)
        chk("V3 按钮进入禁用", b.ev("document.querySelector('[data-shot=\"S01\"] .sb-vid').disabled"), True)
        b.click_video("S01")          # 连点：应被守卫挡住
        time.sleep(0.5)
        chk("V4 只提交 1 次", len(records()) - before, 1)
        rec = records()[-1]
        chk("V4 模型", rec["model"], "minimaxH3")
        chk("V4 档位", (rec["resolution"], rec["duration"]), ("720p", 8))
        chk("V4 比例跟随首帧", rec["ratio"], "adaptive")
        chk("V4 走首帧通道", rec["role"], "first_frame")
        chk("V4 首帧裁成 9:16", rec["frame_size"] and abs(rec["frame_size"][0] / rec["frame_size"][1] - 9 / 16) < 0.01, True)
        chk("V4 带上了 Bearer", rec["auth"], "bearer")

        chk("V5 轮询到成片", b.wait_shot("S01", "vidStatus", "done", 120), True)
        st = b.evj("window.__qaStoryboard()")
        chk("V5 产物是本地 mp4", str(st["shots"][0]["vidUrl"]).startswith("/assets/generated/vid-cgt-mock-"), True)
        chk("V5 记录耗时", st["shots"][0]["vidMs"] > 0, True)
        chk("V5 video 元素可用", b.evj("(()=>{const v=document.querySelector('[data-shot=\"S01\"] .sb-video');"
                                      "return {hidden:v.hidden, src:(v.getAttribute('src')||'').slice(0,24)}})()"),
            {"hidden": False, "src": "/assets/generated/vid-cg"})
        time.sleep(1.0)
        chk("V5 视频可解码", b.ev("(()=>{const v=document.querySelector('[data-shot=\"S01\"] .sb-video');"
                                  "return v.videoWidth > 0 && v.duration > 0})()"), True)
        b.shot("01-one-video")

        # ---------------- 失败：上游 failed ----------------
        b.js("document.querySelector('[data-shot=\"S02\"] .sb-edit').click()")
        time.sleep(0.4)
        b.js("(()=>{const t=document.querySelector('#sbEditDesc');t.value='__MOCK_FAIL__ 车库雨夜，女子撑伞走向电梯，镜头缓慢前推';return 1})()")
        b.js("document.querySelector('#sbEditSave').click()")
        time.sleep(0.3)
        b.js("document.querySelector('[data-shot=\"S02\"] .sb-gen').click()")
        chk("V6 S02 先出图", b.wait_shot("S02", "status", "done", 90), True)
        b.click_video("S02")
        chk("V6 上游失败落到卡片", b.wait_shot("S02", "vidStatus", "error", 120), True)
        chk("V6 按钮变重试", b.ev("document.querySelector('[data-shot=\"S02\"] .sb-vid').textContent"), "重试视频")
        chk("V6 不产生视频文件", b.evj("window.__qaStoryboard().shots[1].vidUrl"), None)
        b.shot("02-video-failed")

        # ---------------- 失败：成片转存失败 ----------------
        b.js("document.querySelector('[data-shot=\"S03\"] .sb-edit').click()")
        time.sleep(0.4)
        b.js("(()=>{const t=document.querySelector('#sbEditDesc');t.value='__MOCK_NODL__ 车库雨夜，女子走向电梯';return 1})()")
        b.js("document.querySelector('#sbEditSave').click()")
        time.sleep(0.3)
        b.js("document.querySelector('[data-shot=\"S03\"] .sb-gen').click()")
        chk("V7 S03 先出图", b.wait_shot("S03", "status", "done", 90), True)
        b.click_video("S03")
        chk("V7 转存失败要报错", b.wait_shot("S03", "vidStatus", "error", 120), True)
        chk("V7 错误说清原因", "转存失败" in (b.evj("window.__qaStoryboard().shots[2].vidErr") or ""), True)

        # ---------------- 存档 ----------------
        b.open(MOCK, "v2", clear=False)
        st = b.evj("window.__qaStoryboard()")
        chk("V9 刷新后视频仍在", str(st["shots"][0]["vidUrl"]).startswith("/assets/generated/vid-cgt-mock-"), True)
        chk("V9 刷新后 video 元素有源",
            b.ev("(document.querySelector('[data-shot=\"S01\"] .sb-video')||{}).hidden"), False)
        chk("V9 底部统计含视频数", "视频" in (st["progress"] or ""), True)

        # ---------------- 本集成片（本地 ffmpeg） ----------------
        chk("V11 有视频后可拼本集", b.ev("document.querySelector('#sbReelBtn').disabled"), False)
        chk("V11 按钮显示段数", b.ev("document.querySelector('#sbReelBtn').textContent"), "预览本集（1 段）")
        b.js("document.querySelector('#sbReelBtn').click()")
        reel = None
        for _ in range(60):
            time.sleep(0.5)
            r = b.evj("window.__qaStoryboard().reel")
            if r and r.get("url"):
                reel = r
                break
        chk("V12 拼接完成", bool(reel), True)
        chk("V12 成片路径", str((reel or {}).get("url")).startswith("/assets/generated/reel-ep1-"), True)
        chk("V12 段数", (reel or {}).get("segments"), 1)
        chk("V12 播放器有源", b.ev("document.querySelector('#sbReelVideo').getAttribute('src')"),
            (reel or {}).get("url"))
        time.sleep(0.8)
        chk("V12 成片可解码", b.ev("(()=>{const v=document.querySelector('#sbReelVideo');"
                                  "return v.videoWidth>0 && v.duration>0})()"), True)
        code, bad = post_reel(["/etc/passwd"])
        chk("V13 非法路径被拒", code, 400)
        code, bad = post_reel([])
        chk("V13 空列表被拒", code, 400)

        # 再加一段：验证多段顺序拼接（mock 每段 2s ⇒ 合计约 4s）
        chk("V14 S04 出图", b.gen_image("S04"), True)
        b.click_video("S04")
        chk("V14 S04 出视频", b.wait_shot("S04", "vidStatus", "done", 120), True)
        chk("V14 按钮变 2 段", b.ev("document.querySelector('#sbReelBtn').textContent"), "预览本集（2 段）")
        b.js("document.querySelector('#sbReelBtn').click()")
        reel2 = None
        for _ in range(90):
            time.sleep(0.5)
            r = b.evj("window.__qaStoryboard().reel")
            if r and r.get("segments") == 2:
                reel2 = r
                break
        chk("V14 两段拼接", (reel2 or {}).get("segments"), 2)
        chk("V14 时长≈4s", 3.4 <= float((reel2 or {}).get("seconds") or 0) <= 4.6, True)
        b.shot("04-reel")

        # ---------------- 生成设置（转场/字幕/片头片尾/底音） ----------------
        b.js("document.querySelector('#sbGear').click()")
        time.sleep(0.4)
        chk("V15 设置弹层打开", b.ev("!!document.querySelector('.popover .sb-chip')"), True)
        chk("V15 计费档默认禁用",
            b.ev("document.querySelector('[data-tier=\"paid\"]').disabled"), True)
        b.js("document.querySelector('[data-tr=\"0.5\"]').click()")
        time.sleep(0.4)
        chk("V15 转场已切 0.5s", b.evj("window.__qaStoryboard().reelOpts.transition"), 0.5)
        b.js("document.querySelector('[data-flag=\"subtitles\"]').click()")
        time.sleep(0.4)
        b.js("document.querySelector('[data-flag=\"titles\"]').click()")
        time.sleep(0.4)
        b.js("document.querySelector('[data-flag=\"music\"]').click()")
        time.sleep(0.4)
        o = b.evj("window.__qaStoryboard().reelOpts")
        chk("V15 字幕/片头/底音已开", [o["subtitles"], o["titles"], o["music"]],
            [True, True, "drone"])
        b.js("document.querySelector('#popoverLayer .pop-scrim').click()")
        time.sleep(0.3)
        chk("V15 设置进存档", b.ev("JSON.parse(localStorage.getItem('jimeng-storyboard-v1')).reelOpts.titles"), True)

        # ---------------- 带选项重拼 ----------------
        old_url = (b.evj("window.__qaStoryboard().reel") or {}).get("url")
        b.js("document.querySelector('#sbReelBtn').click()")
        reel3 = None
        for _ in range(120):
            time.sleep(0.5)
            r = b.evj("window.__qaStoryboard().reel")
            if r and r.get("url") and r.get("url") != old_url:
                reel3 = r
                break
        chk("V16 带选项拼接完成", bool(reel3), True)
        # 1.8 片头 + 2 + 2 段 + 1.8 片尾 − 3×0.5 转场 = 6.1s
        chk("V16 时长含片头片尾与转场", 5.4 <= float((reel3 or {}).get("seconds") or 0) <= 6.8, True)
        chk("V16 成片可解码", b.ev("(()=>{const v=document.querySelector('#sbReelVideo');"
                                  "return v.videoWidth>0 && v.duration>0})()"), True)
        b.shot("05-reel-options")

        # ---------------- 镜序与增删 ----------------
        before = b.evj("window.__qaStoryboard().shots.map(s=>s.desc)")
        chk("V17 初始 6 镜", len(before), 6)
        b.js("document.querySelector('[data-shot=\"S02\"] .sb-edit').click()")
        time.sleep(0.4)
        chk("V17 编辑弹层有上移/下移/删除",
            b.ev("!!document.querySelector('#sbEditUp') && !!document.querySelector('#sbEditDown') "
                 "&& !!document.querySelector('#sbEditDel')"), True)
        b.js("document.querySelector('#sbEditUp').click()")
        time.sleep(0.5)
        after = b.evj("window.__qaStoryboard().shots.map(s=>s.desc)")
        chk("V17 上移换了顺序", [after[0], after[1]], [before[1], before[0]])
        chk("V17 镜号按新顺序重排", b.evj("window.__qaStoryboard().shots.map(s=>s.id)"),
            ["S01", "S02", "S03", "S04", "S05", "S06"])
        moved = b.evj("(()=>{const s=window.__qaStoryboard().shots.find(x=>x.vidUrl);"
                      "return s?[s.desc,s.id]:null})()")
        chk("V17 视频跟着镜头内容走、镜号按新位置", moved, [before[0], "S02"])
        b.js("document.querySelector('#sbAddShot').click()")
        time.sleep(0.5)
        chk("V17 加一镜", len(b.evj("window.__qaStoryboard().shots")), 7)
        b.js("document.querySelector('[data-shot=\"S07\"] .sb-edit').click()")
        time.sleep(0.4)
        b.js("document.querySelector('#sbEditDel').click()")
        time.sleep(0.5)
        chk("V17 删一镜", len(b.evj("window.__qaStoryboard().shots")), 6)
        b.shot("06-reordered")

        # ---------------- 计费档门禁 ----------------
        code, resp = post_video_tier("paid")
        chk("V18 计费档默认被拒", code, 403)
        chk("V18 拒绝原因可读", "AVM_ALLOW_PAID" in (resp.get("error") or ""), True)
        code, resp = post_video_tier("bogus")
        chk("V18 非法档位按免费处理", (code, resp.get("ok")), (200, True))

        # ---------------- 未配置实例 ----------------
        b.open(NOAVM, "v3")
        b.make_script()
        b.gen_image("S01")
        chk("V8 未配置时按钮禁用",
            b.ev("document.querySelector('[data-shot=\"S01\"] .sb-vid').disabled"), True)
        chk("V8 没视频时不能拼本集", b.ev("document.querySelector('#sbReelBtn').disabled"), True)
        title = b.ev("document.querySelector('[data-shot=\"S01\"] .sb-vid').title") or ""
        chk("V8 禁用原因可见", ("AVM_COOKIE" in title) or ("未配置" in title), True)

        # ---------------- 手机 ----------------
        b.act("emulate", "--width", "390", "--height", "844", "--dpr", "2", "--mobile")
        time.sleep(0.4)
        b.open(MOCK, "v4")
        b.make_script()
        b.gen_image("S01")
        chk("V10 390 无横向溢出", b.ev("document.documentElement.scrollWidth > innerWidth + 1"), False)
        chk("V10 390 按钮可见", b.ev("document.querySelector('[data-shot=\"S01\"] .sb-vid').hidden"), False)
        b.shot("03-mobile")
    finally:
        subprocess.run(["bsk", "session", "stop", "--session", b.s, "--json"], capture_output=True)

    ok = sum(1 for r in results if r["ok"])
    REPORT.write_text(json.dumps({"at": time.strftime("%F %T"), "passed": ok,
                                  "total": len(results), "items": results},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{ok}/{len(results)} passed -> {REPORT.relative_to(ROOT)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
