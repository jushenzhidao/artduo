#!/usr/bin/env python3
"""Agent 规划输出的纯函数收敛测试（零网络、零生成）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

CASES = []


def chk(name, got, want):
    ok = got == want
    CASES.append({"name": name, "ok": ok, "got": got, "want": want})
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  got={got!r} want={want!r}"))


def main():
    n = server.normalize_agent
    d = n({"reply": "好", "plan": [{"kind": "image", "prompt": "a", "ratio": "16:9", "count": 3}]})
    chk("标准结构", (d["reply"], d["plan"][0]["ratio"], d["plan"][0]["count"]), ("好", "16:9", 3))

    d = n({"回复": "中文键", "方案": [{"类型": "视频", "提示词": "推镜", "比例": "9:16", "数量": 9}]})
    chk("中文键名 + 视频数量强制 1", (d["reply"], d["plan"][0]["kind"], d["plan"][0]["count"]), ("中文键", "video", 1))

    d = n({"reply": "x", "plan": [{"kind": "audio", "prompt": "p"}]})
    chk("未知 kind 归到图片", d["plan"][0]["kind"], "image")

    d = n({"reply": "x", "plan": [{"kind": "video", "prompt": "p", "ratio": "7:5"}]})
    chk("非法比例回落到 9:16", d["plan"][0]["ratio"], "9:16")

    d = n({"reply": "x", "plan": [{"kind": "video", "prompt": "p", "ratio": "bogus", "count": 2}]})
    chk("视频比例回落 + 数量 1", (d["plan"][0]["ratio"], d["plan"][0]["count"]), ("9:16", 1))

    d = n({"reply": "x", "plan": ["纯字符串方案"]})
    chk("字符串方案可收敛", (d["plan"][0]["kind"], d["plan"][0]["prompt"]), ("image", "纯字符串方案"))

    d = n({"reply": "x", "plan": [{"prompt": str(i)} for i in range(5)]})
    chk("方案最多 3 条", len(d["plan"]), 3)

    d = n({"reply": "", "plan": [{"prompt": "p"}]})
    chk("缺 reply 时补默认文案", bool(d["reply"]), True)

    chk("空对象返回 None", n({}), None)
    chk("非对象返回 None", n("nope"), None)
    chk("无有效 prompt 返回 None", n({"reply": "", "plan": [{"kind": "image", "prompt": ""}]}), None)

    d = n({"reply": "x", "plan": [{"kind": "video", "prompt": "p" * 900}]})
    chk("提示词截到 600 字", len(d["plan"][0]["prompt"]), 600)

    passed = sum(1 for c in CASES if c["ok"])
    import json
    (ROOT / "qa" / "agent-unit-report.json").write_text(
        json.dumps({"passed": passed, "total": len(CASES), "checks": CASES}, ensure_ascii=False, indent=1))
    print(f"\n{passed}/{len(CASES)} passed -> qa/agent-unit-report.json")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
