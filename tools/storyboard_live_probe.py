#!/usr/bin/env python3
"""真实网关单点验证：第 2 集出一张真图 + 刷新不丢。

为什么单独一个工具
------------------
`storyboard_check.py` 跑一轮要 6 次真实调用；改版后只想验证「第 2 集的提示词
确实不同、真实图能落盘并在刷新后恢复」时，1 次调用就够。**这个脚本会真实计费**
（1 次），跑之前先看 `--dry` 提示。

    python3 tools/storyboard_live_probe.py --dry   # 只打印将要做什么
    python3 tools/storyboard_live_probe.py         # 真跑 1 次
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from storyboard_check import B, LIVE, SHOTS, calls_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "qa" / "storyboard-ep2-report.json"


def main() -> int:
    if "--dry" in sys.argv:
        print("[dry] 将打开", LIVE, "→ 写创意 → 切到第 2 集 → 点 S01 生成 1 张 → 刷新校验")
        print("[dry] 预计真实调用：1（会计费）")
        return 0

    b = B()
    results = []

    def chk(name, got, want):
        ok = got == want
        results.append({"name": name, "got": got, "want": want, "ok": ok})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {got!r} (期望 {want!r})")

    try:
        b.open(LIVE, "ep2-live", clear=True)
        before = calls_of(LIVE)
        b.make_script()
        chk("E1 第 1 集提示词已生成", b.ev("window.__qaStoryboard().prompts.length"), 6)
        ep1_prompt = b.evj("window.__qaStoryboard().prompts[0]")

        b.js("document.querySelector('#sbNext').click()")
        time.sleep(0.5)
        chk("E2 已切到第 2 集", b.ev("window.__qaStoryboard().ep"), 2)
        chk("E2 第 2 集提示词与第 1 集不同",
            b.evj("window.__qaStoryboard().prompts[0]") != ep1_prompt, True)

        b.js("document.querySelector('[data-shot=\"S01\"] .sb-gen').click()")
        chk("E3 第 2 集真实出图", b.wait_status("S01", "done", 240), True)
        st = b.evj("window.__qaStoryboard()")
        chk("E3 只发 1 次真实请求", calls_of(LIVE) - before, 1)
        chk("E3 标注真实模型", st["shots"][0]["model"], "flux-3-image")
        chk("E3 全剧进度 1/60", [st["boardDone"], st["boardTotal"]], [1, 60])
        chk("E3 图片解码 1024",
            b.evj("(()=>{const i=document.querySelector('.sb-card[data-status=\"done\"] .sb-img');"
                  "return i ? [i.naturalWidth, i.naturalHeight] : null})()"), [1024, 1024])
        b.js("document.querySelector('#sbSection').scrollIntoView({block:'start'})")
        time.sleep(0.5)
        b.shot("12-live-ep2")

        b.open(LIVE, "ep2-live-reload")
        st2 = b.evj("window.__qaStoryboard()")
        chk("E4 刷新后停在第 2 集", st2["ep"], 2)
        chk("E4 刷新后仍有这张图", [st2["done"], st2["boardDone"]], [1, 1])
        chk("E4 图片文件还在（真的落盘）",
            b.evj("(()=>{const i=document.querySelector('.sb-card[data-status=\"done\"] .sb-img');"
                  "return !!i && i.complete && i.naturalWidth === 1024})()"), True)
    finally:
        subprocess.run(["bsk", "session", "stop", "--session", b.s, "--json"], capture_output=True)

    ok = sum(1 for r in results if r["ok"])
    REPORT.write_text(json.dumps({"at": time.strftime("%F %T"), "calls": 1,
                                  "passed": ok, "total": len(results), "items": results},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{ok}/{len(results)} passed -> {REPORT.relative_to(ROOT)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
