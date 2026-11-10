#!/usr/bin/env python3
"""多形态 × N 次端到端 · bsk 抓包 · 对抗审查。

第一性原理
----------
一次「生成」的不可再分成本是 **上游 1 次计费请求**。所以多形态测试要证伪的
不是「能不能出图」，而是下面这些与「形态」无关的不变式：

  P0 构建一致   浏览器实际执行的 app.js == 磁盘上的 app.js（杜绝缓存假阳性）
  P1 请求恒定   无论比例/分辨率/张数/时长/提示词形态，上游请求体的
                model/size/resolution/n/response_format 恒定；每次提交恰好 1 个请求；
                prompt 原样透传。
  P2 模式守卫   只有 图片 + 链路就绪 才出站；视频 / Agent / 演示 / 积分不足 /
                空提示词 一律零出站；视口与注入形态必须精确生效。
  P3 标签诚实   MOCK 产物标 MOCK、演示标 DEMO；结果页参数必须是真实链路参数
                （1024×1024 · 1K · 1 张），不得拿前端演示参数冒充。
  P4 三方对账   浏览器侧(L1) = 代理侧(L2) = 上游侧(L3)，请求体与凭据指纹逐字段一致。
  P5 失败透明   502 / 非 JSON / URL / 空 data / 坏 base64 如实报错，不降级为结果。
  P6 密钥隔离   真实凭据与 mock 凭据都不出现在浏览器抓包与 DOM 里。
  P7 渲染健康   无横向溢出、预览层不常驻、结果图真实解码。

三层证据
--------
  L1  bsk debug start/requests/export  浏览器真实发出的请求与响应
  L2  qa/trace.jsonl                   代理收到的请求 / 构造的上游请求 / 响应 / 产物哈希
  L3  qa/mock-upstream.jsonl           mock 上游实收的请求体（脱敏）

零计费
------
上游指向 127.0.0.1 的 mock，代理使用假凭据；真实网关调用次数全程不增加。

驱动协议（bsk 0.4.2 的硬约束，踩过的坑都写在这里）
--------------------------------------------------
1. 每个动作前必须 `cooperate sync`，并使用**本次**返回的顶层 observation_id；
   一个 observation 只能消费一次，复用会得到 permission_denied。
2. 必须用 `session start --width/--height` 自建 Agent Window。挂到用户标签页会被
   「The user is holding control of this tab」打断，且视口受用户真实窗口污染。
3. `emulate` 是 per-tab 的，新建标签页不会继承，每张新标签都要重新 emulate。
4. bsk 的控制悬浮层在部分坐标会截获 CDP 鼠标事件，因此主驱动路径用键盘：
   默认按 Enter（产品现在的默认发送键），F23 单独回归 Ctrl+Enter，F22 用 DOM click 覆盖按钮。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
CAP = QA / "captures"
FACTS_DIR = QA / "facts"
SHOTS = QA / "shots" / "e2e"
TRACE = QA / "trace.jsonl"
UPSTREAM = QA / "mock-upstream.jsonl"
LOGFILE = QA / "generate-log.jsonl"
REPORT = QA / "e2e-report.json"
BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8788")   # 指向 mock 实例可零计费回归

# 1×1 PNG：用作参考图夹具，够小、且能被代理的 data URI 校验接受
REF_1PX = ("data:image/png;base64,"
           "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
REAL_KEY_FILE = Path.home() / "PycharmProjects/AI/reverse/bfl/bfl2openai/.env"
BUILD_FILES = ("index.html", "styles.css", "app.js")

CONSTANT_FIELDS = {
    "model": "flux-3-image", "size": "1024x1024", "resolution": "1k",
    "n": 1, "response_format": "b64_json",
}


# ------------------------------------------------------------------ 形态矩阵
def C(cid, name, prompt, *, w=1440, h=900, theme="dark", form=None,
      req=1, src="MOCK", alert=False, no_result=False, hint_kw="", hint_not="",
      presses=1, submit="key", note="", refs_n=None):
    return dict(id=cid, name=name, prompt=prompt, w=w, h=h, theme=theme,
                form=form or {}, presses=presses, submit=submit, refs_n=refs_n,
                expect=dict(req=req, src=src, alert=alert, no_result=no_result,
                            hint_kw=hint_kw, hint_not=hint_not), note=note)


CASES = [
    C("F01", "基线 · 中文短提示词 / 1440 深色", "一只白瓷茶杯放在木桌上，柔和侧光"),
    C("F02", "英文长提示词 / 1440 深色",
      "A cinematic wide shot of a lone lighthouse on a stormy cliff at dusk, volumetric fog, "
      "long exposure, teal and amber grade, highly detailed, 35mm film, no text, no watermark"),
    C("F03", "中文 + emoji + 引号 + 换行 / 1440 浅色",
      '「雨夜」霓虹小巷 🌧️\n湿漉漉的石板路 "reflection" \'单引号\'', theme="light"),
    # textarea 有 maxlength=2000 的硬上限（本轮测出来的真实约束），这里取边界值
    C("F04", "提示词上限 2000 字（验证不截断、不放大）", ("赛博城市夜景，" * 334)[:2000]),
    C("F05", "比例 16:9 / 1K / 1 张 / 细节低", "远山与湖泊的清晨",
      form={"ratio": "16:9", "resolution": "1K", "detail": "低", "count": 1}),
    C("F06", "比例 9:16 / 2K超高 / 8 张（不得放大成 8 次请求）", "竖版人像海报",
      form={"ratio": "9:16", "resolution": "2K", "detail": "超高", "count": 8}),
    C("F07", "移动端 390 深色 / 中文", "窗台上的一只橘猫", w=390, h=844),
    C("F08", "移动端 390 浅色 / 英文", "Minimal ceramic vase on linen", w=390, h=844, theme="light"),
    C("F09", "带 2 张参考图（data URI，必须真的转发）", "把参考图里的红方块放到白瓷杯旁",
      form={"refs": [{"name": "参考图 1", "url": REF_1PX},
                     {"name": "参考图 2", "url": REF_1PX}]}, refs_n=2),
    C("F10", "视频生成模式（应零出站）", "夏日草地上的少女逆风奔跑",
      form={"mode": "video", "typeLabel": "视频生成", "model": "即梦 Seedance 2.0 mini"},
      req=0, src="DEMO", note="flux-3-image 仅出图，视频必须是演示"),
    C("F11", "Agent 模式（应零出站）", "帮我做一个品牌视觉方案",
      form={"mode": "agent", "typeLabel": "Agent 模式", "model": "即梦 Agent"},
      req=0, src="DEMO"),
    C("F12", "前端演示开关（应零出站）", "一只纸鹤", form={"demo": True}, req=0, src="DEMO"),
    C("F13", "演示模式 + 积分不足（应被守卫拦截）", "赛博城市夜景",
      form={"demo": True, "credits": 10}, req=0, src="NONE", no_result=True,
      hint_kw="积分不足", note="需要 800 积分、余额 10，应阻止生成"),
    C("F14", "上游 HTTP 502（如实报错、不重试、不降级）", "__MOCK_502__ 一只猫",
      alert=True, no_result=True, src="NONE"),
    C("F15", "上游返回非 JSON", "__MOCK_NONJSON__ 一只猫", alert=True, no_result=True, src="NONE"),
    C("F16", "上游返回 URL 而非内联图片", "__MOCK_URL__ 一只猫", alert=True, no_result=True, src="NONE"),
    C("F17", "上游返回空 data[]", "__MOCK_EMPTY__ 一只猫", alert=True, no_result=True, src="NONE"),
    C("F18", "上游返回非法 base64", "__MOCK_BADB64__ 一只猫", alert=True, no_result=True, src="NONE"),
    C("F19", "上游慢响应 2 秒（如实耗时、无请求放大）", "__MOCK_SLOW__ 雪山日出"),
    C("F20", "空白 + Ctrl+Enter（走拦截分支，零出站）", "   ", req=0, src="NONE",
      no_result=True, hint_kw="请输入", submit="ctrl"),
    C("F21", "连点两次（busy 守卫，必须只发 1 个请求）", "一枚黄铜齿轮", presses=2),
    C("F22", "发送按钮 DOM click 路径（非快捷键）", "木质书架上的旧望远镜", submit="dom"),
    C("F23", "Ctrl+Enter 旧快捷键仍可发送", "旧快捷键回归", submit="ctrl"),
    C("F24", "空白 + 回车（只换行，不误报错）", "   ", req=0, src="NONE",
      no_result=True, hint_not="请输入"),
    C("F25", "上游要求必须带图（少发即 400）", "__MOCK_REQUIRE_REFS__ 把黄鸭放进杯子",
      form={"refs": [{"name": "画布", "url": REF_1PX}]}, refs_n=1),
]


# ------------------------------------------------------------------ bsk 封装
class Bsk:
    def __init__(self, sid: str):
        self.s = sid
        self.calls = 0

    def raw(self, *args, timeout=120, out_file: Path | None = None):
        self.calls += 1
        cmd = ["bsk", *args]
        fh = open(out_file, "w", encoding="utf-8") if out_file else None
        try:
            if fh:
                r = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, text=True, timeout=timeout)
            else:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        finally:
            if fh:
                fh.close()
        raw = "" if out_file else (r.stdout or "").strip()
        data = {}
        if raw:
            try:
                data = json.loads(raw, strict=False)
            except json.JSONDecodeError:
                data = {"__raw": raw[:400]}
        return data, r.returncode

    def sync(self):
        d, rc = self.raw("cooperate", "sync", "--session", self.s, "--policy", "target", "--json")
        if not d.get("context_id") or not d.get("observation_id"):
            raise RuntimeError(f"sync 失败 rc={rc}: {str(d)[:200]}")
        return d["context_id"], d["observation_id"]

    def act(self, cmd, *args, tab=None, timeout=120, must=True, attempts=4):
        """一次 sync + 一个动作：bsk 的 observation 是一次性的，必须严格配对。

        页面在动画/重绘时会让「刚同步的观察」立刻过期（strict observation changed），
        这不是页面缺陷，重同步后重试即可；但绝不对「已提交的生成」重试。
        """
        extra = ["--tab-id", str(tab)] if tab else []
        d, rc = {}, 1
        for i in range(attempts):
            ctx, obs = self.sync()
            d, rc = self.raw(cmd, "--session", self.s, "--context", ctx, "--observation", obs,
                             *extra, *args, "--json", timeout=timeout)
            msg = str(d.get("message", ""))
            if d.get("code") == "permission_denied" and "observation" in msg and i + 1 < attempts:
                time.sleep(0.2)
                continue
            break
        d["_rc"] = rc
        if must and is_error(d, rc):
            raise RuntimeError(f"{cmd} 失败 rc={rc}: {str(d)[:260]}")
        return d

    def plain(self, *args, timeout=120, out_file: Path | None = None):
        d, rc = self.raw(*args, timeout=timeout, out_file=out_file)
        d["_rc"] = rc
        return d

    def ev(self, expr, tab=None):
        return self.act("evaluate", expr, tab=tab).get("value")

    def facts(self, tab):
        raw = self.ev("JSON.stringify(window.__qaFacts())", tab=tab)
        return json.loads(raw)


# ------------------------------------------------------------------ 工具
ERROR_CODES = {"permission_denied", "invalid_params", "invalid_request", "not_found",
               "timeout", "internal_error", "unknown_error", "target_not_found",
               "observation_required", "action_failed"}


def is_error(d: dict, rc: int) -> bool:
    """bsk 的 `code` 字段与按键码同名（press 会返回 code:"Enter"），不能一概当错误。"""
    if rc != 0 or d.get("exit_code"):
        return True
    return isinstance(d.get("code"), str) and d.get("code") in ERROR_CODES


def jline_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for x in path.read_text(encoding="utf-8").splitlines() if x.strip())


_TRACE_PORT: int | None = None


def trace_port() -> int:
    """trace 是全项目共享文件：只统计打向本实例端口的记录，否则并发实例会互相污染。"""
    global _TRACE_PORT
    if _TRACE_PORT is None:
        from urllib.parse import urlparse
        u = urlparse(BASE)
        _TRACE_PORT = u.port or (443 if u.scheme == "https" else 80)
    return _TRACE_PORT


def norm_prompt(value) -> str:
    """与服务端 normalize_prompt 同口径：去首尾空白、折叠 3 行以上空行。"""
    return re.sub(r"\n{3,}", "\n\n", str(value or "").strip())


def pick_traces(traces: list[dict], prompt: str) -> list[dict]:
    """trace 里有 prompt_head：只认属于本条提示词的记录。"""
    key = norm_prompt(prompt)
    if not key:
        return []
    out = []
    for t in traces:
        head = norm_prompt((t.get("incoming") or {}).get("prompt_head"))
        if head and key.startswith(head):
            out.append(t)
    return out


def pick_ups(ups: list[dict], prompt: str) -> list[dict]:
    """mock 上游记录里有完整 prompt：精确归属，慢用例的迟到请求不会再算到下一条。"""
    key = norm_prompt(prompt)
    if not key:
        return []
    return [u for u in ups if norm_prompt((u.get("body") or {}).get("prompt")) == key]


def read_since(path: Path, since_iso: str) -> list[dict]:
    """按记录自带的 ts 取窗口。比行号切片稳：并发写、迟到落盘都不会错位。"""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(d.get("ts") or "") >= since_iso:
            out.append(d)
    return out


def slice_lines(path: Path, start: int) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines()[start:]:
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if path == TRACE:
            host = ((d.get("incoming") or {}).get("headers") or {}).get("Host", "")
            if host and not host.endswith(f":{trace_port()}"):
                continue
        out.append(d)
    return out


def disk_build() -> dict:
    out = {}
    for n in BUILD_FILES:
        f = ROOT / n
        b = f.read_bytes()
        out[n] = {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
    return out


def real_key() -> str:
    try:
        import re
        m = re.search(r"^\s*BFL2OPENAI_API_KEY\s*=\s*(.*?)\s*$",
                      REAL_KEY_FILE.read_text(encoding="utf-8"), re.M)
        return m.group(1).strip().strip('"').strip("'") if m else ""
    except OSError:
        return ""


def wait_ready(b: Bsk, tab, timeout=20.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = b.ev("JSON.stringify([typeof window.__qaFacts, document.readyState,"
                        " window.__qaSnapshot ? window.__qaSnapshot().statusReady : null])", tab=tab)
            got = json.loads(last)
            # 必须等 /api/status 返回：否则页面还在「链路未知」，注入形态会被当成演示模式
            if got[:2] == ["function", "complete"] and got[2] is True:
                return {"ok": True}
        except Exception:
            pass
        time.sleep(0.2)
    return {"ok": False, "last": last}


def wait_settle(b: Bsk, tab, timeout=20.0) -> dict:
    """等到页面进入终态：非 busy 且出现结果 / 错误条 / 守卫提示。"""
    deadline = time.monotonic() + timeout
    facts = None
    while time.monotonic() < deadline:
        try:
            facts = b.facts(tab)
        except Exception:
            time.sleep(0.2)
            continue
        snap, hint = facts["snapshot"], facts["hint"]
        terminal = (not snap["busy"]) and (
            facts["resultCards"] > 0 or bool(facts["alerts"])
            or "不足" in hint or "请输入" in hint or "失败" in hint
        )
        if terminal:
            return facts
        time.sleep(0.25)
    return facts or {}


# ------------------------------------------------------------------ 单用例
def run_case(b: Bsk, case: dict, build_ok: bool, tab) -> dict:
    cid = case["id"]
    form = dict(case["form"])
    # 每例唯一 URL：同 URL 导航会被浏览器当成空操作，导致上一例的状态残留
    url = f"{BASE}/?theme={case['theme']}&case={cid}"
    # 用记录自带的时间戳划窗口：行号切片在多实例并发写时会错位
    since = datetime.now(timezone.utc).isoformat()

    steps = [f"tab={tab}"]
    try:
        # 裸导航最快；只有被要求控制上下文时才补一次 sync 再导航。
        # 反过来会死锁：标签停在 about: 时 sync 直接报「Page frames cannot be observed」。
        # bsk 偶尔在「媒体源刚变过」时拒绝一次导航
        # （permission_denied: The media source changed; synchronize and replan）：
        # 同步一次再重排即可，最多试 3 轮，不动页面状态。
        nav = {}
        for _ in range(3):
            nav = b.plain("navigate", "--session", b.s, "--tab-id", str(tab), url, "--json", timeout=60)
            if nav.get("reached") == "load":
                break
            try:
                b.sync()
            except Exception:
                pass
            time.sleep(0.4)
            nav = b.act("navigate", url, tab=tab, must=False)
            if nav.get("reached") == "load":
                break
            time.sleep(0.4)
        steps.append(f"nav={nav.get('reached')}")
        if nav.get("reached") != "load":
            raise RuntimeError(f"导航未生效: {str(nav)[:200]}")
        ready = wait_ready(b, tab)
        steps.append(f"ready={ready['ok']}")
        if not ready["ok"]:
            raise RuntimeError(f"页面未就绪: {ready}")
        # 任务队列与历史都会持久化：先清掉上一轮的痕迹再重载，
        # 否则 wait_settle 会看到上一例的卡片而提前判定终态。
        b.ev("localStorage.removeItem('jimeng-jobs-v1'), "
             "localStorage.removeItem('jimeng-sessions-v1'), 1", tab=tab)
        b.plain("tab", "reload", "--session", b.s, str(tab), "--json", timeout=60)
        time.sleep(1.2)
        wait_ready(b, tab)
        landed = b.ev("location.search", tab=tab)
        if f"case={cid}" not in str(landed):
            raise RuntimeError(f"页面未完成重载，仍停留在 {landed}")

        # 采集绑定「当前活动标签页」；带上别的 tab_id 会触发
        # 「stop the task's current capture before selecting another tab」
        b.plain("debug", "stop", "--session", b.s, "--json")
        st = b.plain("debug", "start", "--session", b.s, "--name", cid, "--json")
        steps.append(f"capture={bool((st.get('run') or {}).get('id'))}")

        # 精确视口（emulate 是 per-tab 的，必须在新标签上重设）
        dpr = 2 if case["w"] <= 640 else 1
        emu_args = ["--width", str(case["w"]), "--height", str(case["h"]), "--dpr", str(dpr)]
        if case["w"] <= 640:
            emu_args.append("--mobile")
        b.act("emulate", *emu_args, tab=tab)
        steps.append(f"emulate={case['w']}x{case['h']}@{dpr}")

        # 精确注入形态（纯内存，零额外请求）
        expr = "(() => { const f = %s; return window.__qaApply(f); })()" % json.dumps(form, ensure_ascii=False)
        applied = b.ev(expr, tab=tab)
        steps.append(f"apply={applied}")
        base_snap = json.loads(b.ev("JSON.stringify(window.__qaSnapshot())", tab=tab))

        # 输入提示词
        b.act("fill", "--selector", "#prompt", "--value", case["prompt"], tab=tab)
        steps.append("fill")

        # 提交：快捷键 → 产品自身文档化的路径；dom → 按钮自身 onclick
        if case["submit"] == "dom":
            b.ev("document.querySelector('#sendBtn').click(), true", tab=tab)
            steps.append("dom-click")
        else:
            # 主路径是纯 Enter（产品现在的默认交互）；ctrl 用来回归旧快捷键
            key = "Control+Enter" if case["submit"] == "ctrl" else "Enter"
            for i in range(case["presses"]):
                b.act("press", key, "--selector", "#prompt", tab=tab)
                steps.append(f"press:{key}#{i + 1}")
                if i + 1 < case["presses"]:
                    time.sleep(0.15)

        facts = wait_settle(b, tab)
        steps.append(f"settled busy={facts.get('snapshot', {}).get('busy')}")

        shot = SHOTS / f"{cid}.png"
        b.act("screenshot", "--out", str(shot), tab=tab, must=False)

        # L1 浏览器侧：必须在 debug stop 之前读，否则录制已封存
        # 不再按 URL 过滤抓取（过滤参数会把 /api/tasks 一起挡掉），拿到全部再筛
        req_out = b.plain("debug", "requests", "--session", b.s,
                          "--limit", "80", "--json")
        # 前端已改成异步任务链路（POST /api/tasks），同步 /api/generate 保留兼容
        l1 = [r for r in (req_out.get("requests") or [])
              if r.get("method") == "POST"
              and ("/api/generate" in r.get("url", "")
                   or r.get("url", "").split("?")[0].endswith("/api/tasks"))]

        # export 必须在 stop 之前：stop 会销毁采集
        cap = CAP / f"{cid}.json"
        b.plain("debug", "export", "--session", b.s, "--json", out_file=cap)
        b.plain("debug", "stop", "--session", b.s, "--json")
        (FACTS_DIR / f"{cid}.json").write_text(
            json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")

        traces = pick_traces(read_since(TRACE, since), case["prompt"])
        ups = pick_ups(read_since(UPSTREAM, since), case["prompt"])
        return build_result(case, facts, base_snap, l1, traces, ups, cap, shot, steps, build_ok)
    finally:
        b.plain("debug", "stop", "--session", b.s, "--json")


def build_result(case, facts, base_snap, l1, traces, ups, cap, shot, steps, build_ok) -> dict:
    cid, exp = case["id"], case["expect"]
    n1, n2, n3 = len(l1), len(traces), len(ups)
    snap = (facts or {}).get("snapshot", {})
    tags = (facts or {}).get("sourceTags", [])
    alerts = (facts or {}).get("alerts", [])
    bubble = (facts or {}).get("bubbleTags", [])
    hint = (facts or {}).get("hint", "")
    cards = (facts or {}).get("resultCards", 0)

    checks = {}
    checks["P0_构建一致"] = build_ok

    # P1 请求恒定
    bodies = [u.get("body") for u in ups if isinstance(u.get("body"), dict)]
    checks["P1_出站次数符合形态"] = (n1 == n2 == n3 == exp["req"])
    checks["P1_恰好一个上游请求"] = n3 <= 1
    checks["P1_请求体恒定"] = all(all(bd.get(k) == v for k, v in CONSTANT_FIELDS.items())
                                 for bd in bodies) if bodies else (exp["req"] == 0)
    if bodies:
        checks["P1_提示词原样透传"] = bodies[0].get("prompt") == case["prompt"]
    else:
        checks["P1_提示词原样透传"] = exp["req"] == 0

    # P2 形态与守卫
    checks["P2_视口精确"] = (base_snap.get("vw") == case["w"]
                            and base_snap.get("vh") == case["h"])
    expect_snap = {k: v for k, v in case["form"].items() if k not in ("demo", "refs")}
    if "refs" in case["form"]:
        expect_snap["refs"] = len(case["form"]["refs"])
    checks["P2_形态注入生效"] = all(base_snap.get(k) == v for k, v in expect_snap.items())
    if case["form"].get("demo"):
        checks["P2_演示开关生效"] = base_snap.get("apiReal") is False

    # P3 标签诚实
    if exp["src"] == "NONE":
        checks["P3_无结果标签"] = len(tags) == 0
    else:
        checks["P3_来源标签正确"] = bool(tags) and all(exp["src"] in x for x in tags)
    if exp["req"] and not exp["alert"]:
        checks["P3_真实参数不冒充"] = ("1024×1024 · 1K" in bubble and "1 张" in bubble)
        checks["P3_真实模型标真实"] = any("flux-3-image" in x for x in bubble)
    if cards > 0:
        checks["P3_结果图已解码"] = all((facts or {}).get("imgOk") or [False])
    if exp["src"] == "MOCK":
        checks["P3_说明为零计费"] = "零计费" in (facts or {}).get("convDesc", "")

    # P8 参考图：既要真发出去，也要如实标注
    if case.get("refs_n") is not None:
        want = case["refs_n"]
        imgs = ((traces[-1].get("upstream_request") or {}).get("image_summary") or []) if traces else []
        checks["P8_参考图已转发"] = len(imgs) == want
        if ups:
            checks["P8_上游收到参考图"] = (ups[-1].get("refs_count") == want
                                        and len(ups[-1].get("refs") or []) == want)
        else:
            checks["P8_上游收到参考图"] = want == 0
        checks["P8_参考图标签诚实"] = (f"{want} 个参考" in " ".join(bubble)) if want else True
        if want:
            checks["P8_参考图体积合理"] = all(0 < (x.get("bytes") or 0) <= 6 * 1024 * 1024 for x in imgs)

    # P4 三方对账
    checks["P4_三方请求数相等"] = (n1 == n2 == n3)
    pair_ok = True
    for t in traces:
        a = (t.get("upstream_request") or {}).get("body")
        if a and not any(u.get("body") == a for u in ups):
            pair_ok = False
    checks["P4_构造体与实收体一致"] = pair_ok
    fp_ok = True
    for t in traces:
        a = ((t.get("upstream_request") or {}).get("headers") or {}).get("Authorization")
        if a and not any(((u.get("headers") or {}).get("Authorization")) == a for u in ups):
            fp_ok = False
    checks["P4_凭据指纹一致"] = fp_ok
    checks["P4_模式标记为图片"] = all((t.get("incoming") or {}).get("mode") == "image" for t in traces)

    # P5 失败透明
    checks["P5_错误条符合预期"] = (bool(alerts) == exp["alert"])
    checks["P5_失败未降级为结果"] = (not exp["no_result"]) or cards == 0
    if exp["hint_kw"]:
        checks["P5_守卫提示正确"] = exp["hint_kw"] in hint
    if exp["hint_not"]:
        checks["P5_空回车不误报"] = exp["hint_not"] not in hint

    # P6 密钥隔离
    key = real_key()
    blob = ""
    if cap.exists():
        blob += cap.read_text(encoding="utf-8", errors="replace")
    blob += json.dumps(facts, ensure_ascii=False)
    blob += json.dumps(traces, ensure_ascii=False)
    blob += json.dumps(ups, ensure_ascii=False)
    checks["P6_真实凭据未泄漏"] = (not key) or (key not in blob)
    checks["P6_mock凭据未泄漏"] = "MOCK-LOCAL-ONLY" not in blob

    # P7 渲染健康
    checks["P7_无横向溢出"] = not (facts or {}).get("overflow", True)
    checks["P7_预览层未常驻"] = (facts or {}).get("lightboxDisplay") == "none"

    return {
        "id": cid, "name": case["name"], "note": case["note"],
        "viewport": f"{case['w']}x{case['h']}", "theme": case["theme"],
        "form": case["form"], "submit": case["submit"], "presses": case["presses"],
        "prompt_len": len(case["prompt"]), "prompt_head": case["prompt"][:50].replace("\n", "\\n"),
        "expect_req": exp["req"],
        "counts": {"L1_browser": n1, "L2_proxy": n2, "L3_upstream": n3},
        "l1_status": [r.get("status") for r in l1],
        "l3_scenario": [u.get("scenario") for u in ups],
        "snapshot": snap, "base_snapshot": base_snap,
        "dom": {k: v for k, v in (facts or {}).items() if k != "snapshot"},
        "steps": steps,
        "checks": checks,
        "ok": all(checks.values()),
        "captures": {"L1": str(cap.relative_to(ROOT)), "L2": "qa/trace.jsonl",
                     "L3": "qa/mock-upstream.jsonl", "shot": str(shot.relative_to(ROOT)),
                     "facts": f"qa/facts/{cid}.json"},
    }


# ------------------------------------------------------------------ 主流程
def guard_no_live(base: str, need_llm_off: bool = False, need_video_off: bool = False) -> None:
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
    if need_video_off and st.get("video", {}).get("configured") and os.environ.get("ALLOW_LIVE") != "1":
        print(f"拒绝启动：{base} 的视频链路已配置，F10（视频模式应零出站）会真的提交一条任务。\n"
              "  · 用 AVM_COOKIE_FILE=/tmp/no-such-cookie.txt 启动实例，再跑回归\n"
              "  · 确实要跑真实视频，显式设 ALLOW_LIVE=1", file=sys.stderr)
        raise SystemExit(2)
    if need_llm_off and st.get("llm", {}).get("configured") and os.environ.get("ALLOW_LIVE") != "1":
        print(f"拒绝启动：{base} 的文本模型开着，模板断言会变成模型输出。\n"
              "  · 用 JIMENG_LLM=off 启动实例，或设 ALLOW_LIVE=1 显式放行", file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    guard_no_live(BASE, need_video_off=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", help="只跑指定用例，逗号分隔，如 F01,F14")
    ap.add_argument("--session", help="复用已有 bsk session id（需已建 Agent Window）")
    ap.add_argument("--keep-open", action="store_true", help="结束后保留会话")
    args = ap.parse_args()

    for d in (QA, CAP, FACTS_DIR, SHOTS):
        d.mkdir(parents=True, exist_ok=True)

    cases = CASES
    if args.cases:
        want = {x.strip() for x in args.cases.split(",")}
        cases = [c for c in CASES if c["id"] in want]
    if not cases:
        print("没有匹配的用例", file=sys.stderr)
        return 2

    # 会话：必须是自建 Agent Window（不劫持用户标签页）
    sid, owned = args.session, False
    if not sid:
        d, rc = Bsk("").raw("session", "start", "--json", "--browser", "90d43dd3",
                            "--width", "1700", "--height", "1300", "--no-focus",
                            "--name", "jimeng-multiform-e2e")
        sid = d.get("session_id")
        owned = True
        if not sid:
            print(f"启动会话失败 rc={rc}: {str(d)[:200]}", file=sys.stderr)
            return 2
    b = Bsk(sid)
    print(f"[e2e] session={sid} cases={len(cases)} upstream=mock(零计费)", flush=True)

    # P0：一次性校验「浏览器跑的 == 磁盘上的」
    build_probe_tab = None
    build_info = {"disk": disk_build()}
    try:
        td = b.plain("tab", "create", "--session", sid, "--url", f"{BASE}/?theme=dark", "--json")
        build_probe_tab = td.get("tab_id")
        wait_ready(b, build_probe_tab)
        served = json.loads(b.ev("fetch('/api/build').then(r=>r.json()).then(d=>JSON.stringify(d.files))",
                                 tab=build_probe_tab))
        transfer = b.ev("""(() => {
            const e = performance.getEntriesByType('resource').find(r => r.name.endsWith('/app.js'));
            return e ? Math.round(e.transferSize) : -1;
        })()""", tab=build_probe_tab)
        build_info.update({
            "served": served,
            "app_js_transfer_bytes": transfer,
            "served_matches_disk": all(
                served.get(n, {}).get("sha256", "")[:16] == build_info["disk"][n]["sha256"][:16]
                for n in BUILD_FILES),
            "not_from_cache": isinstance(transfer, int) and transfer > 0,
        })
    except Exception as exc:
        build_info["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if build_probe_tab:
            b.plain("tab", "close", "--session", sid, str(build_probe_tab), "--json", timeout=60)

    build_ok = bool(build_info.get("served_matches_disk") and build_info.get("not_from_cache"))

    live_before = sum(1 for x in LOGFILE.read_text(encoding="utf-8").splitlines()
                      if '"upstream_mode": "live"' in x) if LOGFILE.exists() else 0

    # 全程复用同一标签页：采集是 per-tab 的，换来换去会触发「先停旧采集」冲突
    # 起始就必须是真实页面：about:blank 上无法 cooperate sync
    work_tab = b.plain("tab", "create", "--session", sid, "--url", f"{BASE}/?theme=dark",
                       "--json").get("tab_id")
    if not work_tab:
        print("创建测试标签页失败", file=sys.stderr)
        return 2

    results = []
    try:
        for i, case in enumerate(cases, 1):
            t = time.monotonic()
            try:
                r = run_case(b, case, build_ok, work_tab)
            except Exception as exc:
                r = {"id": case["id"], "name": case["name"], "ok": False,
                     "error": f"{type(exc).__name__}: {exc}", "checks": {}}
            results.append(r)
            c = r.get("counts", {})
            print(f"  [{i:02d}/{len(cases)}] {r['id']} {'PASS' if r['ok'] else 'FAIL'} "
                  f"L1/L2/L3={c.get('L1_browser', '-')}/{c.get('L2_proxy', '-')}/{c.get('L3_upstream', '-')} "
                  f"{time.monotonic() - t:.1f}s" + ("" if r["ok"] else f"  {r.get('error', '')}"), flush=True)
    finally:
        b.plain("tab", "close", "--session", sid, str(work_tab), "--json", timeout=60)
        if owned and not args.keep_open:
            b.plain("session", "stop", sid, "--json", timeout=60)

    live_after = sum(1 for x in LOGFILE.read_text(encoding="utf-8").splitlines()
                     if '"upstream_mode": "live"' in x) if LOGFILE.exists() else 0

    agg = {}
    for r in results:
        for k, v in (r.get("checks") or {}).items():
            agg.setdefault(k, []).append(bool(v))
    passed = sum(1 for r in results if r.get("ok"))
    summary = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": "bsk 0.4.2（evaluate/emulate/press/debug/screenshot）+ 本地代理 trace + mock 上游记录",
        "session": sid,
        "upstream_mode": "mock（127.0.0.1:8799，零计费）",
        "build": build_info,
        "cases": len(results), "passed": passed, "failed": len(results) - passed,
        "checks": {k: {"pass": sum(v), "total": len(v)} for k, v in sorted(agg.items())},
        "live_log_entries_before": live_before, "live_log_entries_after": live_after,
        "new_billed_calls": live_after - live_before,
        "constant_fields": CONSTANT_FIELDS,
        "results": results,
    }
    REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[e2e] {passed}/{len(results)} PASS | 新增真实计费调用 = {live_after - live_before} "
          f"| 构建一致 = {build_ok}")
    print(f"[e2e] 报告 -> {REPORT}")
    for k, v in summary["checks"].items():
        flag = "" if v["pass"] == v["total"] else "  <-- 需修复"
        print(f"  {k}: {v['pass']}/{v['total']}{flag}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
