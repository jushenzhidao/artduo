#!/usr/bin/env python3
"""扩展视图的确定性自检：探索 / 资产 / AI 工具 / 主体 / 动效。

零生成请求：只调用媒体库、本地工具（PIL/ffmpeg）与主体 CRUD。
所有会改文件的操作都作用在专用夹具上，并在结束时恢复。
"""
from __future__ import annotations

import base64
import io
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from qa_cdp import CDP, start_chrome  # noqa: E402

BASE = "http://127.0.0.1:8801/"
SHOTS = ROOT / "qa" / "shots" / "views"
REPORT = ROOT / "qa" / "views-report.json"
GEN = ROOT / "assets" / "generated"
FIXTURE = "fixture-views-800x600.png"
FIXTURE2 = "fixture-views-640x640.png"

CHECKS = []


def check(name: str, ok: bool, detail=None):
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))


def make_fixtures():
    from PIL import Image, ImageDraw
    if not (GEN / FIXTURE).exists():
        im = Image.new("RGB", (800, 600))
        d = ImageDraw.Draw(im)
        for y in range(600):
            d.line([(0, y), (800, y)], fill=(20 + y // 8, 40 + y // 12, 90 + y // 6))
        d.ellipse((260, 160, 540, 440), fill=(240, 170, 90))
        d.rectangle((40, 40, 200, 120), fill=(90, 220, 200))
        im.save(GEN / FIXTURE)
    if not (GEN / FIXTURE2).exists():
        im = Image.new("RGB", (640, 640), (30, 30, 36))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((120, 120, 520, 520), 40, fill=(120, 90, 220))
        im.save(GEN / FIXTURE2)


def b64_of(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def wait(cdp: CDP, expr: str, timeout=25, interval=0.25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if cdp.eval(expr):
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def js(cdp: CDP, expr: str, await_promise=False):
    return cdp.eval(expr, await_promise=await_promise)


def goto_nav(cdp: CDP, nav: str):
    js(cdp, f"""(() => {{
      const b=[...document.querySelectorAll('.nav-item')].find(x=>x.dataset.nav==={json.dumps(nav)});
      if(!b) throw new Error('nav missing {nav}');
      b.click(); return true;
    }})()""")
    time.sleep(0.8)


def main() -> int:
    make_fixtures()
    baseline = {p.name for p in GEN.iterdir() if p.is_file()}
    proc, ws = start_chrome()
    try:
        cdp = CDP(ws)
        cdp.cmd("Runtime.enable")
        cdp.navigate(BASE, 1440, 900)
        js(cdp, "window.confirm = () => true; window.__errs=[]; window.addEventListener('error',e=>window.__errs.push(String(e.message)));")

        # ---------------- 探索
        goto_nav(cdp, "探索")
        ok = wait(cdp, "document.querySelectorAll('#vxGrid .vx-card').length >= 12")
        stats = js(cdp, "(() => ({cards:document.querySelectorAll('#vxGrid .vx-card').length, stats:document.querySelector('#vxStats')?.textContent.replace(/\\s+/g,' '), overflow:document.documentElement.scrollWidth>innerWidth+1}))()")
        check("探索：作品网格渲染", ok and stats["cards"] >= 12, stats)
        check("探索：无横向溢出", not stats["overflow"], stats)
        cdp.shot(SHOTS / "10-explore.png")

        # 视频筛选
        js(cdp, "document.querySelector('#vxTabs [data-t=\"video\"]').click()")
        time.sleep(1.2)
        vids = js(cdp, "(() => ({n:document.querySelectorAll('#vxGrid .vx-card').length, allVideo:[...document.querySelectorAll('#vxGrid .vx-media')].every(x=>!!x.querySelector('video'))}))()")
        check("探索：视频筛选只剩视频", vids["n"] > 0 and vids["allVideo"], vids)

        # 搜索
        js(cdp, "document.querySelector('#vxTabs [data-t=\"all\"]').click()")
        time.sleep(1.2)
        js(cdp, """(() => { const q=document.querySelector('#vxQ'); q.value='雨'; q.dispatchEvent(new Event('input',{bubbles:true})); return true; })()""")
        time.sleep(1.4)
        sr = js(cdp, "(() => ({n:document.querySelectorAll('#vxGrid .vx-card').length, prompt:[...document.querySelectorAll('#vxGrid .vx-prompt')].map(x=>x.textContent)}))()")
        check("探索：搜索能命中提示词", sr["n"] > 0 and all("雨" in p for p in sr["prompt"]), sr)
        js(cdp, """(() => { const q=document.querySelector('#vxQ'); q.value=''; q.dispatchEvent(new Event('input',{bubbles:true})); return true; })()""")
        time.sleep(1.4)

        # 排序：体积最大
        js(cdp, "(() => { const s=document.querySelector('#vxSort'); s.value='big'; s.dispatchEvent(new Event('change',{bubbles:true})); return true; })()")
        time.sleep(1.4)
        sizes = js(cdp, "[...document.querySelectorAll('#vxGrid .vx-sub')].slice(0,8).map(x=>x.textContent)")
        def kb(t):
            import re
            m = re.search(r"([\d.]+)\s*(MB|KB|B)", t or "")
            if not m:
                return 0
            v = float(m.group(1))
            return v * (1024 * 1024 if m.group(2) == "MB" else 1024 if m.group(2) == "KB" else 1)
        nums = [kb(t) for t in sizes]
        check("探索：体积排序生效", nums == sorted(nums, reverse=True), sizes)

        # 打开详情
        js(cdp, "document.querySelector('#vxGrid .vx-card').click()")
        time.sleep(0.7)
        det = js(cdp, "(() => ({open:!!document.querySelector('.vdlg'), prompt:document.querySelector('.vdlg-prompt')?.textContent.slice(0,30), actions:[...document.querySelectorAll('.vdlg-actions .vp-btn')].map(b=>b.textContent.trim())}))()")
        check("探索：详情弹层有提示词与动作", det["open"] and len(det["actions"]) >= 3, det)
        cdp.shot(SHOTS / "11-explore-detail.png")
        js(cdp, "document.querySelector('.vdlg-head [data-dlg-close]').click()")
        time.sleep(0.4)

        # 做同款
        js(cdp, "document.querySelector('#vxGrid .vx-card').click()")
        time.sleep(0.5)
        js(cdp, "document.querySelector('[data-d=\"same\"]').click()")
        time.sleep(0.7)
        same = js(cdp, "(() => ({view:document.querySelector('#app').className, prompt:document.querySelector('#prompt')?.value.slice(0,20), vpage:document.querySelector('#vpage')?.hidden}))()")
        check("探索：做同款回填提示词并回创作页", same["prompt"] and same["vpage"] and "view-vpage" not in same["view"], same)

        # ---------------- 资产
        goto_nav(cdp, "资产")
        wait(cdp, "document.querySelectorAll('#vaGrid .vx-card').length >= 12")
        js(cdp, "document.querySelector('#vaQ').value='fixture'; document.querySelector('#vaQ').dispatchEvent(new Event('input',{bubbles:true}))")
        time.sleep(1.4)
        fcards = js(cdp, "document.querySelectorAll('#vaGrid .vx-card').length")
        check("资产：能按文件名筛选出夹具", fcards >= 2, fcards)
        js(cdp, "document.querySelector('#vaSelect').click()")
        time.sleep(1.4)
        js(cdp, "document.querySelectorAll('#vaGrid .vx-card')[0].click(); document.querySelectorAll('#vaGrid .vx-card')[1].click()")
        time.sleep(0.5)
        batch = js(cdp, "(() => ({bar:!!document.querySelector('.vp-batch'), txt:document.querySelector('.vp-batch b')?.textContent}))()")
        check("资产：多选后出现批量条", batch["bar"] and batch["txt"] == "已选 2", batch)
        cdp.shot(SHOTS / "12-assets-select.png")

        # 批量收藏
        js(cdp, "document.querySelector('.vp-batch [data-b=\"fav\"]').click()")
        time.sleep(1.5)
        favs = js(cdp, "(async () => (await (await fetch('/api/library?type=all&fav=1&limit=100')).json()).items.map(x=>x.id))()", await_promise=True)
        check("资产：批量收藏写入服务端", any(f in favs for f in [FIXTURE, FIXTURE2]), favs[:6])

        # 移入回收站 + 恢复
        js(cdp, "document.querySelector('#vaSelect').click()")
        time.sleep(1.0)
        js(cdp, "document.querySelector('#vaQ').value='fixture-views-640x640'; document.querySelector('#vaQ').dispatchEvent(new Event('input',{bubbles:true}))")
        time.sleep(1.5)
        js(cdp, "document.querySelector('#vaSelect').click()")
        time.sleep(1.2)
        js(cdp, "document.querySelectorAll('#vaGrid .vx-card')[0].click()")
        time.sleep(0.3)
        js(cdp, "document.querySelector('.vp-batch [data-b=\"trash\"]').click()")
        time.sleep(1.6)
        left = js(cdp, "document.querySelectorAll('#vaGrid .vx-card').length")
        check("资产：移入回收站后列表减少", left == 0, left)
        js(cdp, "document.querySelector('#vaTabs [data-t=\"trash\"]').click()")
        time.sleep(1.3)
        tr = js(cdp, "(() => ({n:document.querySelectorAll('#vaGrid .vx-card').length, restore:document.querySelectorAll('[data-restore]').length}))()")
        check("资产：回收站可见且可恢复", tr["n"] >= 1 and tr["restore"] >= 1, tr)
        cdp.shot(SHOTS / "13-assets-trash.png")
        js(cdp, "document.querySelector('[data-restore]').click()")
        time.sleep(1.6)
        tr2 = js(cdp, "document.querySelectorAll('#vaGrid .vx-card').length")
        check("资产：恢复后回收站清空该文件", tr2 == tr["n"] - 1, {"before": tr["n"], "after": tr2})

        # ---------------- AI 工具
        goto_nav(cdp, "AI工具")
        js(cdp, "document.querySelector('[data-tool=\"crop\"]').click()")
        time.sleep(0.6)
        js(cdp, "document.querySelector('#vtPick').click()")
        time.sleep(1.2)
        pick = js(cdp, "(() => ({open:!!document.querySelector('.vpick'), n:document.querySelectorAll('.vpick button').length}))()")
        check("工具：素材选择器能打开", pick["open"] and pick["n"] > 0, pick)
        js(cdp, """(() => { const q=document.querySelector('#vpPickQ'); q.value='fixture'; q.dispatchEvent(new Event('input',{bubbles:true})); return true; })()""")
        time.sleep(0.9)
        picked = js(cdp, """(() => {
          const b=[...document.querySelectorAll('.vpick button')].find(x=>(x.title||'').includes('fixture')||(x.querySelector('img')?.src||'').includes('fixture'));
          if(!b) return false; b.click(); return true;
        })()""")
        time.sleep(1.2)
        check("工具：选中本机素材", picked and js(cdp, "!!document.querySelector('#vtSlot img')"), picked)
        js(cdp, "document.querySelector('#vtRun').click()")
        ok = wait(cdp, "!!document.querySelector('#vtOut img')", 30)
        out = js(cdp, "(() => ({ok:!!document.querySelector('#vtOut img'), src:document.querySelector('#vtOut img')?.getAttribute('src'), w:document.querySelector('#vtOut img')?.naturalWidth, note:document.querySelector('#vtOut .vt-note')?.textContent}))()")
        check("工具：裁切产出可解码图片", ok and out["w"] and out["w"] > 0, out)
        cdp.shot(SHOTS / "14-tools-crop.png")

        # 压缩
        js(cdp, "document.querySelector('[data-tool=\"compress\"]').click()")
        time.sleep(0.6)
        js(cdp, "document.querySelector('#vtPick').click()")
        time.sleep(0.9)
        js(cdp, """(() => { const q=document.querySelector('#vpPickQ'); q.value='fixture'; q.dispatchEvent(new Event('input',{bubbles:true})); return true; })()""")
        time.sleep(0.9)
        js(cdp, """(() => { const b=[...document.querySelectorAll('.vpick button')].find(x=>(x.title||'').includes('fixture')||(x.querySelector('img')?.src||'').includes('fixture')); b.click(); return true; })()""")
        time.sleep(0.9)
        js(cdp, "document.querySelector('#vtRun').click()")
        ok = wait(cdp, "!!document.querySelector('#vtOut img')", 30)
        comp = js(cdp, "document.querySelector('#vtOut .vt-note')?.textContent")
        check("工具：压缩产出并报告质量", ok and "q" in (comp or ""), comp)

        # 抽帧 + GIF
        for tool, kw in (("frame", "首帧"), ("gif", "fps")):
            js(cdp, f"document.querySelector('[data-tool=\"{tool}\"]').click()")
            time.sleep(0.5)
            js(cdp, "document.querySelector('#vtPick').click()")
            time.sleep(0.9)
            js(cdp, "(() => { const q=document.querySelector('#vpPickQ'); q.value='vid-'; q.dispatchEvent(new Event('input',{bubbles:true})); return true; })()")
            time.sleep(0.9)
            js(cdp, "(() => { const b=[...document.querySelectorAll('.vpick button')].find(x=>x.querySelector('video')); if(!b) return false; b.click(); return true; })()")
            time.sleep(0.9)
            js(cdp, "document.querySelector('#vtRun').click()")
            ok = wait(cdp, "!!document.querySelector('#vtOut img, #vtOut video')", 40)
            note = js(cdp, "document.querySelector('#vtOut .vt-note')?.textContent")
            check(f"工具：{tool} 产出可播放结果", ok and (kw in (note or "") or tool == "frame"), note)
        cdp.shot(SHOTS / "15-tools-gif.png")

        # ---------------- 主体
        goto_nav(cdp, "主体")
        wait(cdp, "!!document.querySelector('#vsNew')")
        js(cdp, "document.querySelector('#vsNew').click()")
        time.sleep(0.5)
        payload = b64_of(GEN / FIXTURE2)
        js(cdp, f"""(() => {{
          const inp=document.querySelector('#vsFile'); const dt=new DataTransfer();
          const bin=atob({json.dumps(payload)}); const arr=new Uint8Array(bin.length);
          for(let i=0;i<bin.length;i++) arr[i]=bin.charCodeAt(i);
          dt.items.add(new File([arr], 'ref.png', {{type:'image/png'}}));
          inp.files=dt.files; inp.dispatchEvent(new Event('change',{{bubbles:true}}));
          document.querySelector('#vsName').value='自检主体';
          document.querySelector('#vsNote').value='views_check 创建';
          return true;
        }})()""")
        time.sleep(1.4)
        pre = js(cdp, "document.querySelectorAll('#vsRefs img').length")
        check("主体：上传参考图出现预览", pre == 1, pre)
        js(cdp, "document.querySelector('#vsSave').click()")
        time.sleep(2.0)
        sub = js(cdp, "(async () => (await (await fetch('/api/subjects')).json()).items.find(x=>x.name==='自检主体'))()", await_promise=True)
        check("主体：创建写入服务端并有封面", bool(sub) and bool(sub.get("cover")), sub)
        check("主体：卡片出现在列表", "自检主体" in (js(cdp, "document.querySelector('#vpBody')?.textContent") or ""))
        cdp.shot(SHOTS / "16-subjects.png")

        # 用于创作：参考图应进入创作页的参考区
        js(cdp, """(() => { const c=[...document.querySelectorAll('[data-sid]')].find(x=>x.textContent.includes('自检主体')); if(!c) return false; c.querySelector('[data-act="use"]').click(); return true; })()""")
        ok = wait(cdp, "document.querySelectorAll('#refTray .ref-chip img').length >= 1", 25)
        refs = js(cdp, "(() => ({n:document.querySelectorAll('#refTray .ref-chip').length, vpage:document.querySelector('#vpage')?.hidden, hint:document.querySelector('#hintText')?.textContent}))()")
        check("主体：用于创作把参考图放进创作页", ok and refs["vpage"], refs)
        cdp.shot(SHOTS / "16b-subject-refs.png")

        # 删除（confirm 已 mock 为 true）
        goto_nav(cdp, "主体")
        wait(cdp, "!!document.querySelector('#vsNew')")
        js(cdp, """(() => { const c=[...document.querySelectorAll('[data-sid]')].find(x=>x.textContent.includes('自检主体')); if(!c) return false; c.querySelector('[data-act="del"]').click(); return true; })()""")
        time.sleep(1.8)
        gone = js(cdp, "(async () => !(await (await fetch('/api/subjects')).json()).items.some(x=>x.name==='自检主体'))()", await_promise=True)
        check("主体：删除后从服务端消失", bool(gone), gone)
        ok = wait(cdp, "[...document.querySelectorAll('[data-restore-sub]')].length >= 1", 8)
        check("主体：删除后出现在回收站区", ok)
        js(cdp, "document.querySelector('[data-restore-sub]').click()")
        time.sleep(1.8)
        back = js(cdp, "(async () => (await (await fetch('/api/subjects')).json()).items.some(x=>x.name==='自检主体'))()", await_promise=True)
        check("主体：点恢复后回到列表", bool(back), back)
        js(cdp, """(() => { const c=[...document.querySelectorAll('[data-sid]')].find(x=>x.textContent.includes('自检主体')); c?.querySelector('[data-act="del"]').click(); return true; })()""")
        time.sleep(1.2)

        # ---------------- 动效
        goto_nav(cdp, "动效工作室")
        wait(cdp, "!!document.querySelector('#vmPick')")
        js(cdp, "document.querySelector('#vmPick').click()")
        time.sleep(1.0)
        js(cdp, """(() => { const b=[...document.querySelectorAll('.vpick button')].find(x=>x.querySelector('img')); if(!b) return false; b.click(); return true; })()""")
        time.sleep(1.2)
        m = js(cdp, "(() => ({img:!!document.querySelector('#vmStage img'), disabled:document.querySelector('#vmGo')?.disabled, note:document.querySelector('#vmNote')?.textContent, prompt:document.querySelector('#vmPrompt')?.value.slice(0,20)}))()")
        check("动效：选图后显示首帧", m["img"], m)
        check("动效：链路未配置时按钮禁用且说明原因", m["disabled"] and "未配置" in (m["note"] or ""), m)
        js(cdp, "document.querySelector('[data-p=\"pan\"]').click()")
        time.sleep(0.8)
        m2 = js(cdp, "document.querySelector('#vmPrompt')?.value")
        check("动效：切换预设会改写运镜描述", "横移" in (m2 or ""), m2)
        cdp.shot(SHOTS / "17-motion.png")

        # ---------------- Agent 工作台
        cdp.navigate(BASE, 1440, 900)
        js(cdp, "document.querySelector('.mode-tab[data-mode=\"agent\"]').click()")
        time.sleep(0.6)
        entry = js(cdp, "(() => ({has:!!document.querySelector('#agentEntry'), active:document.querySelector('.mode-tab[data-mode=\"agent\"]')?.classList.contains('is-active')}))()")
        check("Agent：切到 Agent 模式出现工作台入口", entry["has"] and entry["active"], entry)
        js(cdp, "document.querySelector('#agentEntry').click()")
        time.sleep(0.6)
        check("Agent：入口能打开工作台", js(cdp, "!!document.querySelector('#agInput')"))
        js(cdp, "document.querySelector('[data-q]').click()")
        ok = wait(cdp, "document.querySelectorAll('.ag-plan').length >= 3", 30)
        plans = js(cdp, "(() => ({n:document.querySelectorAll('.ag-plan').length, reply:document.querySelector('.ag-msg.bot .ag-bubble')?.textContent.slice(0,30), mock:document.querySelector('.ag-meta')?.textContent}))()")
        check("Agent：返回 3 条方案且标注 MOCK", ok and plans["n"] == 3 and "MOCK" in (plans["mock"] or ""), plans)
        cdp.shot(SHOTS / "20-agent.png")
        js(cdp, "document.querySelector('[data-plan=\"1-0\"]').click()")
        time.sleep(0.8)
        fill = js(cdp, "(() => ({prompt:document.querySelector('#prompt')?.value.slice(0,20), mode:document.querySelector('.mode-tab.is-active')?.dataset.mode, vpage:document.querySelector('#vpage')?.hidden}))()")
        check("Agent：填入创作页会带模式与提示词", bool(fill["prompt"]) and fill["mode"] == "image" and fill["vpage"], fill)

        # ---------------- 响应式 / 浅色
        for w, h, theme in ((390, 844, "dark"), (1024, 768, "dark"), (1440, 900, "light")):
            cdp.navigate(BASE + f"?theme={theme}", w, h)
            for nav, key in (("探索", "explore"), ("资产", "assets"), ("AI工具", "tools"), ("主体", "subjects"), ("动效工作室", "motion")):
                goto_nav(cdp, nav)
                time.sleep(0.5)
                ov = js(cdp, "document.documentElement.scrollWidth > innerWidth + 1")
                check(f"布局 {key} @{w}/{theme} 无横向溢出", not ov)
            js(cdp, "window.JimengViews.open('agent')")
            time.sleep(0.4)
            ov = js(cdp, "document.documentElement.scrollWidth > innerWidth + 1")
            check(f"布局 agent @{w}/{theme} 无横向溢出", not ov)
            if w == 390:
                goto_nav(cdp, "探索")
                cdp.shot(SHOTS / "18-explore-390.png")
            if theme == "light":
                goto_nav(cdp, "资产")
                cdp.shot(SHOTS / "19-assets-light.png")

        errs = js(cdp, "window.__errs || []")
        check("运行期无未捕获异常", not errs, errs)
    finally:
        try:
            proc.terminate()
        except Exception:
            pass

    # 清理本次新增的测试产物：移入回收站（可恢复），不让夹具污染作品库
    try:
        import urllib.request
        fresh = [p.name for p in GEN.iterdir()
                 if p.is_file() and p.name not in baseline
                 and (p.name.startswith("tool-") or p.name.startswith("fixture-"))]
        moved = 0
        for i in range(0, len(fresh), 40):
            req = urllib.request.Request(
                BASE + "api/library/trash", data=json.dumps({"ids": fresh[i:i + 40]}).encode(),
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                moved += len(json.loads(r.read().decode()).get("moved", []))
        if moved:
            print(f"cleaned {moved} test artifacts -> trash")
    except Exception as exc:
        print(f"cleanup skipped: {exc}")

    passed = sum(1 for c in CHECKS if c["ok"])
    total = len(CHECKS)
    REPORT.write_text(json.dumps({"passed": passed, "total": total, "checks": CHECKS}, ensure_ascii=False, indent=1))
    print(f"\n{passed}/{total} passed -> {REPORT.relative_to(ROOT)}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
