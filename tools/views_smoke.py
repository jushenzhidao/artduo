#!/usr/bin/env python3
"""新视图冒烟：加载 5 个扩展页，抓 JS 异常 / 控制台错误 / 关键 DOM / 截图。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from qa_cdp import CDP, start_chrome  # noqa: E402

BASE = "http://127.0.0.1:8801/"
SHOTS = ROOT / "qa" / "shots" / "views"
VIEWS = [("探索", "explore"), ("资产", "assets"), ("AI工具", "tools"), ("主体", "subjects"), ("动效工作室", "motion")]


def main() -> int:
    proc, ws = start_chrome()
    fails = []
    try:
        cdp = CDP(ws)
        cdp.cmd("Runtime.enable")
        cdp.cmd("Log.enable")
        cdp.navigate(BASE, 1440, 900)
        # 清空控制台缓冲
        for _ in range(5):
            try:
                cdp.ws.recv()
            except Exception:
                break
        for nav, key in VIEWS:
            ok = cdp.eval(f"""(() => {{
              const b = [...document.querySelectorAll('.nav-item')].find(x => x.dataset.nav === {json.dumps(nav)});
              if (!b) return 'nav-missing';
              b.click();
              return 'clicked';
            }})()""")
            if ok != "clicked":
                fails.append({"view": key, "why": ok})
                continue
            time.sleep(1.6)
            state = cdp.eval("""(() => ({
              vpage: !document.querySelector('#vpage')?.hidden,
              appClass: document.querySelector('#app')?.className,
              crumb: document.querySelector('#crumb')?.textContent,
              activeNav: [...document.querySelectorAll('.nav-item.is-active')].map(x => x.dataset.nav),
              cards: document.querySelectorAll('#vpage .vx-card, #vpage .vt-card, #vpage .vs-card, #vpage .vm-panel').length,
              empty: document.querySelectorAll('#vpage .vp-empty').length,
              err: document.querySelectorAll('#vpage .vt-note').length,
              overflow: document.documentElement.scrollWidth > innerWidth + 1,
              title: document.querySelector('#vpage h1')?.textContent,
            }))()""")
            cdp.shot(SHOTS / f"01-{key}.png")
            if not state or not state.get("vpage"):
                fails.append({"view": key, "why": "vpage 未打开", "state": state})
            elif key != "assets" and not state.get("cards"):
                fails.append({"view": key, "why": "没有渲染出卡片", "state": state})
            print(key, json.dumps(state, ensure_ascii=False))
        # 收集异常
        logs = cdp.eval("""(() => (window.__qaErrors || []).slice(0, 20))()""")
        print("errors:", logs)
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
    print(json.dumps({"fails": fails}, ensure_ascii=False, indent=1))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
