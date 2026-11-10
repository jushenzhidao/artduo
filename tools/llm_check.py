#!/usr/bin/env python3
"""「文本模型」链路自检：证明剧本/镜头真的是模型写的，且关掉时如实退回模板。

两段：

  A 段（默认，零成本）：对 8798（JIMENG_LLM=off）验证
    A1 状态里 llm.configured=false          A2 模型按钮显示 DreamScript
    A3 发送后走模板（source=template）      A4 徽标写「剧本：本地模板」
    A5 不出现「用模型写本集镜头」入口        A6 POST /api/script → 503 + fallback
    A7 空 prompt / 超长 prompt → 400

  B 段（--live，真实调用）：对 8788（LLM 已配置）验证
    B1 状态里模型名与 base 正确             B2 模型按钮显示真实模型名
    B3 发送 → source=llm，徽标写模型名       B4 镜头描述不是模板套话
    B5 副标题写明「按剧情写」并带重写入口     B6 第 2 集默认仍是模板
    B7 点重写 → 第 2 集 source=llm、描述变了  B8 刷新后仍是模型镜头
    B9 模型写作期间有计时提示
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
from video_check import B  # 复用同一套 bsk 驱动

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
SHOTS = QA / "shots" / "llm"
SHOTS.mkdir(parents=True, exist_ok=True)
video_check.SHOTS = SHOTS          # 截图落到 qa/shots/llm/
REPORT = QA / "llm-report.json"

OFFLINE = "http://127.0.0.1:8798"     # JIMENG_LLM=off 的实例
LIVE = "http://127.0.0.1:8788"        # 真实链路（含文本模型）
PROMPT = "帮我写一部都市复仇短剧：女主的父亲被合伙人害死，她改头换面回公司做实习助理，共 6 集。"
TEMPLATE_LINES = ("女主独自走进画面", "男主从暗处走出来", "被推到画面正中", "视线落下去的一瞬")


def post_json(path: str, body: dict, base: str = OFFLINE, timeout: int = 240):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:200]}


def alive(base: str) -> bool:
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def send_and_wait(b: B, want_source: str, timeout: int = 180) -> float:
    """点发送并等到剧本来源变成期望值，返回耗时秒。"""
    b.act("fill", "--selector", "#dramaPrompt", "--value", PROMPT)
    t0 = time.time()
    b.js("document.querySelector('#dramaSend').click()")
    while time.time() - t0 < timeout:
        time.sleep(1.0)
        if b.evj("window.__qaStoryboard?.().scriptSource") == want_source:
            return time.time() - t0
    return time.time() - t0


def main() -> int:
    live = "--live" in sys.argv
    b = B()
    results = []

    def chk(name, got, want):
        ok = got == want
        results.append({"name": name, "got": got, "want": want, "ok": ok})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {got!r} (期望 {want!r})")

    try:
        # ================= A 段：没配模型时如实退回模板 =================
        if not alive(OFFLINE):
            raise RuntimeError(f"{OFFLINE} 没起来：先跑 tools/run_offline.sh 或手动启动 8798（JIMENG_LLM=off）")
        b.open(OFFLINE, "a1")
        st = b.evj("window.__qaStoryboard()")
        chk("A1 状态里 llm 未配置", bool((st.get("llm") or {}).get("configured")), False)
        chk("A2 模型按钮显示占位名", b.ev("document.querySelector('#dramaModel').textContent"), "DreamScript")
        chk("A2 按钮标注为未配置", b.ev("document.querySelector('#dramaModel').dataset.llm"), "off")
        secs = send_and_wait(b, "template", timeout=40)
        chk("A3 走模板且不慢", secs < 10, True)
        chk("A3 剧本来源是模板", b.evj("window.__qaStoryboard().scriptSource"), "template")
        chk("A4 徽标写本地模板",
            b.ev("document.querySelector('.drama-badge-local').textContent"), "剧本：本地模板")
        chk("A4 徽标 title 说明未调模型",
            "未调用文本模型" in (b.ev("document.querySelector('.drama-badge-local').title") or ""), True)
        chk("A5 没有模型重写入口", b.ev("!!document.querySelector('#sbLlmEp')"), False)
        chk("A5 副标题写模板拆解",
            "剧本模板拆解" in (b.ev("document.querySelector('.sb-title p').textContent") or ""), True)
        # 提示词里写死集数时以提示词为准（工具条默认 10 集，不该覆盖用户的「共 6 集」）
        b.act("fill", "--selector", "#dramaPrompt", "--value",
              "帮我写一部都市复仇短剧，女主回公司查父亲的死因，共 6 集。")
        b.js("document.querySelector('#dramaSend').click()")
        time.sleep(1.6)
        chk("A8 集数听提示词", b.evj("window.__qaStoryboard().totalEp"), 6)
        chk("A8 工具条同步", b.ev("document.querySelector('#dramaEpsVal').textContent"), "6")
        code, body = post_json("/api/script", {"prompt": PROMPT, "episodes": 6})
        chk("A6 关掉模型时 503", code, 503)
        chk("A6 带 fallback 标记", body.get("fallback"), True)
        code, body = post_json("/api/script", {"prompt": ""})
        chk("A7 空 prompt 400", code, 400)
        code, body = post_json("/api/script", {"prompt": "长" * 600})
        chk("A7 超长 prompt 400", code, 400)
        code, body = post_json("/api/shots", {"prompt": PROMPT, "ep": 1, "context": {}})
        chk("A7 /api/shots 也 503", code, 503)
        b.shot("01-offline-template")

        # ================= B 段：真实模型 =================
        if live:
            if not alive(LIVE):
                raise RuntimeError(f"{LIVE} 没起来")
            b.open(LIVE, "b1")
            st = b.evj("window.__qaStoryboard()")
            llm = st.get("llm") or {}
            chk("B1 模型已配置", bool(llm.get("configured")), True)
            chk("B1 模型名非空", bool(llm.get("model")), True)
            chk("B1 base 是 loomy", "loomy" in (llm.get("base") or ""), True)
            chk("B2 按钮显示模型名",
                b.ev("document.querySelector('#dramaModel').textContent"), llm.get("model"))
            t0 = time.time()
            secs = send_and_wait(b, "llm", timeout=180)
            chk("B3 走的是模型", b.evj("window.__qaStoryboard().scriptSource"), "llm")
            chk("B3 单次写完整稿 < 120s", secs < 120, True)
            title_after = b.evj("window.__qaStoryboard().scriptTitle")
            chk("B3 片名带书名号", title_after.startswith("《") and len(title_after) > 3, True)
            badge = b.ev("document.querySelector('.drama-badge-local').textContent") or ""
            chk("B3 徽标写真实模型", llm.get("model") in badge, True)
            chk("B3 徽标配色是模型档",
                b.ev("document.querySelector('.drama-badge-local').dataset.src"), "llm")
            descs = b.evj("window.__qaStoryboard().shots.map(s=>s.desc)")
            chk("B4 6 镜", len(descs), 6)
            chk("B4 描述不是模板套话",
                any(t in d for d in descs for t in TEMPLATE_LINES), False)
            chk("B4 描述互不相同", len(set(descs)), 6)
            chk("B5 副标题写明按剧情写",
                "按剧情写" in (b.ev("document.querySelector('.sb-title p').textContent") or ""), True)
            chk("B5 有重写入口", b.ev("!!document.querySelector('#sbLlmEp')"), True)
            chk("B5 入口文案是再让模型重写",
                b.ev("document.querySelector('#sbLlmEp').textContent"), "再让模型重写本集")
            b.js("document.querySelector('#sbSection').scrollIntoView({block:'start'})")
            time.sleep(0.5)
            b.shot("02-live-script")

            # 第 2 集：默认还是模板，点重写才走模型
            b.js("document.querySelector('#sbNext').click()")
            time.sleep(0.9)
            chk("B6 第 2 集默认模板", b.evj("window.__qaStoryboard().epSource"), "template")
            chk("B6 入口文案变成用模型写",
                b.ev("document.querySelector('#sbLlmEp').textContent"), "用模型写本集镜头")
            old = b.evj("window.__qaStoryboard().shots.map(s=>s.desc)")
            t0 = time.time()
            b.js("document.querySelector('#sbLlmEp').click()")
            time.sleep(1.5)
            chk("B9 写作中显示计时",
                "模型写作中" in (b.ev("document.querySelector('#sbLlmEp').textContent") or ""), True)
            while time.time() - t0 < 180:
                time.sleep(1.0)
                if b.evj("window.__qaStoryboard().epSource") == "llm":
                    break
            chk("B7 第 2 集已由模型重写", b.evj("window.__qaStoryboard().epSource"), "llm")
            chk("B7 重写耗时 < 120s", time.time() - t0 < 120, True)
            new = b.evj("window.__qaStoryboard().shots.map(s=>s.desc)")
            chk("B7 镜头描述确实换了", len([d for d in new if d not in old]) >= 4, True)
            chk("B7 模型名写进 epModel",
                b.evj("window.__qaStoryboard().epModel"), llm.get("model"))
            chk("B7 仍是 6 镜", len(new), 6)
            b.js("document.querySelector('#sbSection').scrollIntoView({block:'start'})")
            time.sleep(0.5)
            b.shot("03-live-ep2")

            b.open(LIVE, "b2", clear=False)
            st2 = b.evj("window.__qaStoryboard()")
            chk("B8 刷新后还在第 2 集", st2.get("ep"), 2)
            chk("B8 刷新后仍是模型镜头", st2.get("epSource"), "llm")
            chk("B8 刷新后剧本仍是模型写的", st2.get("scriptSource"), "llm")
            chk("B8 标题一致", st2.get("scriptTitle"), title_after)
            code, body = post_json("/api/shots", {"prompt": ""}, base=LIVE)
            chk("B10 /api/shots 空 prompt 400", code, 400)
            code, body = post_json("/api/script", {"prompt": "长" * 600}, base=LIVE)
            chk("B10 /api/script 超长 prompt 400", code, 400)
    finally:
        subprocess.run(["bsk", "session", "stop", "--session", b.s, "--json"], capture_output=True)

    ok = sum(1 for r in results if r["ok"])
    REPORT.write_text(json.dumps({"at": time.strftime("%F %T"), "live": live, "passed": ok,
                                  "total": len(results), "items": results},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{ok}/{len(results)} passed -> {REPORT.relative_to(ROOT)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
