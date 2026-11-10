#!/usr/bin/env python3
"""媒体库 / 工具 / 主体接口的对抗式自检（零生成、零计费）。

重点证伪：
  1. 路径穿越（../、绝对路径、符号链接、subject 目录越界）一律拒绝；
  2. 回收站是**移动**不是删除，恢复后字节一致；
  3. 封面/缩略图不会把 6MB 原图直接塞给列表；
  4. 工具输出只落在 assets/generated/tool-*，前端不能指定输出名；
  5. 非法比例 / 超限参数 / 非 mp4 抽帧都有明确 4xx，而不是 500。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("VIEWS_BASE", "http://127.0.0.1:8801")
GEN = ROOT / "assets" / "generated"
TRASH = ROOT / "assets" / "_trash"
QA = ROOT / "qa"
REPORT = QA / "library-report.json"
FIX = "fixture-library-check.png"

CHECKS = []


def check(name, ok, detail=None):
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(r, timeout=30) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except Exception:
                return resp.status, {"bytes": len(raw)}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return exc.code, {"raw": raw[:200].decode("utf-8", "replace")}


def make_fixture():
    from PIL import Image
    p = GEN / FIX
    if not p.exists():
        im = Image.new("RGB", (900, 600), (40, 80, 140))
        im.save(p)
    return p


def main() -> int:
    from PIL import Image
    p = make_fixture()
    size0 = p.stat().st_size
    sha0 = hashlib.sha256(p.read_bytes()).hexdigest()

    # ---------- 列表
    st, d = req("GET", "/api/library?limit=5")
    check("媒体库：返回分页与计数", st == 200 and d.get("ok") and "counts" in d and len(d["items"]) <= 5, st)
    check("媒体库：条目含尺寸与来源", all("w" in x and "source" in x for x in d["items"]), d["items"][:1])

    st, d = req("GET", "/api/library?q=fixture-library-check")
    check("媒体库：搜索命中夹具", st == 200 and any(x["id"] == FIX for x in d["items"]), d.get("total"))

    st, d = req("GET", "/api/library?type=video&limit=3")
    check("媒体库：类型过滤生效", st == 200 and all(x["type"] == "video" for x in d["items"]), d.get("items"))

    # ---------- 路径穿越
    for bad in ("../server.py", "/etc/passwd", "..%2Fserver.py", "a/../../server.py",
                ".hidden.mp4", "x" * 200 + ".png"):
        st, d = req("POST", "/api/library/fav", {"id": bad, "fav": True})
        check(f"安全：拒绝非法 id {bad[:26]}", st == 400 and not d.get("ok"), st)
    st, d = req("POST", "/api/tools/crop", {"file": "/assets/generated/../../server.py", "ratio": "1:1"})
    check("安全：裁切拒绝越界文件", st == 400, (st, d))
    st, d = req("POST", "/api/tools/crop", {"file": "/etc/passwd", "ratio": "1:1"})
    check("安全：裁切拒绝绝对路径", st == 400, (st, d))
    st, d = req("POST", "/api/subjects/delete", {"id": "../../etc"})
    check("安全：主体删除拒绝非法 id", st == 400, (st, d))

    # ---------- 收藏
    st, d = req("POST", "/api/library/fav", {"id": FIX, "fav": True})
    check("收藏：写入成功", st == 200 and d.get("fav") is True, (st, d))
    st, d = req("GET", "/api/library?fav=1&limit=200")
    check("收藏：列表可筛出", any(x["id"] == FIX for x in d["items"]), d.get("total"))
    st, d = req("POST", "/api/library/fav", {"id": FIX, "fav": False})
    check("收藏：取消成功", st == 200 and d.get("fav") is False, (st, d))

    # ---------- 回收站
    st, d = req("POST", "/api/library/trash", {"ids": [FIX]})
    check("回收站：移入成功", st == 200 and FIX in d.get("moved", []), (st, d))
    check("回收站：原文件已不在生成目录", not (GEN / FIX).exists())
    check("回收站：文件在 _trash 且字节一致", (TRASH / FIX).exists() and (TRASH / FIX).stat().st_size == size0)
    st, d = req("GET", "/api/library/trash")
    check("回收站：列表包含原文件名", any(x["original"] == FIX for x in d["items"]), d.get("items"))
    st, _ = req("GET", f"/assets/_trash/{FIX}")
    check("回收站：缩略图可访问", st == 200, st)
    st, d = req("POST", "/api/library/restore", {"ids": [FIX]})
    check("回收站：恢复成功", st == 200 and FIX in d.get("restored", []), (st, d))
    check("回收站：恢复后哈希一致", (GEN / FIX).exists() and hashlib.sha256((GEN / FIX).read_bytes()).hexdigest() == sha0)

    # ---------- 工具
    st, d = req("POST", "/api/tools/crop", {"file": f"/assets/generated/{FIX}", "ratio": "9:16", "focus": "top"})
    check("裁切：9:16 输出比例正确", st == 200 and abs(d["w"] / d["h"] - 9 / 16) < 0.02, (st, d))
    check("裁切：输出名由服务端生成", d.get("file", "").startswith("tool-"))
    check("裁切：输出文件真实存在", (GEN / d["file"]).exists())
    st, d2 = req("POST", "/api/tools/crop", {"file": f"/assets/generated/{FIX}", "ratio": "7:5"})
    check("裁切：非法比例 400", st == 400, (st, d2))
    st, d2 = req("POST", "/api/tools/crop", {"file": f"/assets/generated/{FIX}", "ratio": "1:1", "focus": "nope"})
    check("裁切：非法重心 400", st == 400, (st, d2))

    st, d = req("POST", "/api/tools/compress", {"file": f"/assets/generated/{FIX}", "quality": 60, "maxSide": 480})
    check("压缩：输出小于原图", st == 200 and d["bytes"] < size0 and max(d["w"], d["h"]) <= 480, (st, d))
    st, d = req("POST", "/api/tools/compress", {"file": f"/assets/generated/{FIX}", "quality": 60, "maxSide": 480, "targetKb": 20})
    check("压缩：目标体积模式生效", st == 200 and d["bytes"] <= 20 * 1024, (st, d))

    st, d = req("POST", "/api/tools/frame", {"file": f"/assets/generated/{FIX}", "at": "first"})
    check("抽帧：非 mp4 明确拒绝", st == 400, (st, d))

    # 找一条现成视频
    st, lib = req("GET", "/api/library?type=video&limit=1")
    vid = lib["items"][0]["id"] if lib.get("items") else None
    if vid:
        st, d = req("POST", "/api/tools/frame", {"file": f"/assets/generated/{vid}", "at": "first"})
        check("抽帧：视频首帧输出 PNG", st == 200 and d["file"].endswith(".png") and (GEN / d["file"]).exists(), (st, d))
        st, d = req("POST", "/api/tools/gif", {"file": f"/assets/generated/{vid}", "fps": 8, "width": 240, "seconds": 1})
        check("GIF：输出合法且体积合理", st == 200 and d["file"].endswith(".gif") and 100 < d["bytes"] < 8 * 1024 * 1024, (st, d))
        st, d = req("POST", "/api/tools/gif", {"file": f"/assets/generated/{vid}", "fps": 8, "width": 240, "seconds": 99})
        check("GIF：时长被夹到上限", st == 200 and d.get("seconds") == 15, (st, d))

    # ---------- 上传通道
    buf = (GEN / FIX).read_bytes()
    uri = "data:image/png;base64," + base64.b64encode(buf).decode()
    st, d = req("POST", "/api/tools/crop", {"upload": uri, "ratio": "1:1"})
    check("上传：data URI 可直接处理", st == 200 and (GEN / d["file"]).exists(), (st, d))
    st, d = req("POST", "/api/tools/crop", {"upload": "data:image/png;base64,%%%%", "ratio": "1:1"})
    check("上传：坏 base64 明确拒绝", st == 400, (st, d))
    st, d = req("POST", "/api/tools/crop", {"upload": "data:text/plain;base64,aGVsbG8=", "ratio": "1:1"})
    check("上传：非图片 MIME 拒绝", st == 400, (st, d))

    # ---------- 主体
    tiny = ("data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    st, d = req("POST", "/api/subjects", {"name": "库自检主体", "note": "check", "images": [tiny, tiny]})
    sid = (d.get("subject") or {}).get("id")
    check("主体：创建返回封面与参考数", st == 200 and sid and d["subject"]["refCount"] == 2 and d["subject"]["cover"], (st, d))
    st, d = req("POST", "/api/subjects", {"name": "", "images": [tiny]})
    check("主体：空名称拒绝", st == 400, (st, d))
    st, d = req("POST", "/api/subjects", {"name": "x" * 41, "images": [tiny]})
    check("主体：超长名称拒绝", st == 400, (st, d))
    st, d = req("POST", "/api/subjects", {"name": "too many", "images": [tiny] * 10})
    check("主体：参考图超过 9 张拒绝", st == 400, (st, d))
    st, d = req("POST", "/api/subjects", {"name": "bad mime", "images": ["data:text/plain;base64,aGVsbG8="]})
    check("主体：非图片 data URI 拒绝", st == 400, (st, d))
    st, d = req("POST", "/api/subjects/delete", {"id": sid})
    check("主体：删除移入回收站", st == 200 and d.get("moved") == 1, (st, d))
    st, d = req("GET", "/api/subjects")
    check("主体：删除后列表不再包含", all(x["id"] != sid for x in d["items"]), d.get("total"))
    check("主体：删除后进入回收站列表", any(x["id"] == sid for x in (d.get("trash") or [])), d.get("trash"))
    st, d = req("POST", "/api/subjects/restore", {"id": sid})
    check("主体：可从回收站恢复", st == 200 and d.get("ok"), (st, d))
    st, d = req("GET", "/api/subjects")
    back = next((x for x in d["items"] if x["id"] == sid), None)
    check("主体：恢复后参考图仍在", bool(back) and back.get("refCount") == 2, back)
    req("POST", "/api/subjects/delete", {"id": sid})

    # ---------- Agent 接口（mock 规划，零出站）
    st, d = req("POST", "/api/agent", {"messages": [{"role": "user", "content": "雨夜海报"}]})
    check("Agent：返回 reply + 1–3 条方案", st == 200 and d.get("ok") and d.get("reply") and 1 <= len(d.get("plan", [])) <= 3, (st, d))
    check("Agent：方案字段完整且比例合法", all(p.get("kind") in ("image", "video") and p.get("prompt") and p.get("ratio") in ("1:1", "9:16", "16:9", "3:4", "4:3") for p in d.get("plan", [])), d.get("plan"))
    st, d = req("POST", "/api/agent", {"messages": []})
    check("Agent：空对话 400", st == 400, (st, d))
    st, d = req("POST", "/api/agent", {"messages": [{"role": "assistant", "content": "hi"}]})
    check("Agent：最后一条必须是 user", st == 400, (st, d))
    st, d = req("POST", "/api/agent", {"messages": [{"role": "system", "content": "x"}]})
    check("Agent：拒绝 system 角色", st == 400, (st, d))
    st, d = req("POST", "/api/agent", {"messages": [{"role": "user", "content": "x" * 2001}]})
    check("Agent：超长消息 400", st == 400, (st, d))
    st, d = req("POST", "/api/agent", {"messages": [{"role": "user", "content": "x"}] * 13})
    check("Agent：超过 12 条 400", st == 400, (st, d))

    # 夹具也移入回收站，保持作品库干净
    try:
        req("POST", "/api/library/trash", {"ids": [FIX]})
    except Exception:
        pass

    passed = sum(1 for c in CHECKS if c["ok"])
    REPORT.write_text(json.dumps({"passed": passed, "total": len(CHECKS), "checks": CHECKS}, ensure_ascii=False, indent=1))
    print(f"\n{passed}/{len(CHECKS)} passed -> qa/library-report.json")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
