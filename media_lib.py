#!/usr/bin/env python3
"""本机媒体库：扫描 assets/ 下的产物，提供浏览 / 收藏 / 回收站 / 主体库。

设计约束
--------
1. **只读扫描 + 可逆移动**：不提供永久删除。移入回收站只是 `assets/generated/` →
   `assets/_trash/` 的同一文件系统 rename，可原样恢复。
2. **不信任文件名**：所有入口只接受 basename，且必须匹配 `MEDIA_RE`；拼路径后再做
   `resolve()` containment 校验，杜绝 `../` 与符号链接越界。
3. **元数据缓存**：图片尺寸用 PIL（失败则读 PNG IHDR），视频用 ffprobe；缓存按
   `name|size|mtime` 失效，避免每次浏览都 fork 38 个 ffprobe。
4. **提示词来自日志**：`qa/generate-log.jsonl` / `qa/video-log.jsonl` 是唯一事实源；
   查不到就写空，不猜、不编。
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
GENERATED = ASSETS / "generated"
SUBJECTS = ASSETS / "subjects"
TRASH = ASSETS / "_trash"
QA = ROOT / "qa"
STORE_FILE = QA / "library-store.json"
CACHE_FILE = QA / "library-cache.json"
GEN_LOG = QA / "generate-log.jsonl"
VID_LOG = QA / "video-log.jsonl"

MEDIA_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(png|jpg|jpeg|webp|gif|mp4)$", re.I)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXT = {".mp4"}
MAX_SCAN = 5000

_lock = threading.Lock()
_subject_lock = threading.Lock()
_cache: dict | None = None
_store: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure() -> None:
    for d in (GENERATED, SUBJECTS, TRASH, QA):
        d.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ 文件名守卫
def safe_name(name: object) -> str | None:
    """只接受纯文件名；拒绝路径分隔符、控制字符与超长名。"""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    if not MEDIA_RE.match(name):
        return None
    return name


def media_path(name: str, root: Path = GENERATED) -> Path | None:
    """解析到 root 下的真实路径，并确认没有越界（含符号链接）。"""
    n = safe_name(name)
    if not n:
        return None
    p = (root / n).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        return None
    return p


# ------------------------------------------------------------------ 小工具
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load_store() -> dict:
    global _store
    with _lock:
        if _store is None:
            raw = _read_json(STORE_FILE, {})
            _store = {
                "fav": [x for x in raw.get("fav", []) if safe_name(x)],
                "subjects": raw.get("subjects", []),
                "updatedAt": raw.get("updatedAt"),
            }
        return _store


def save_store() -> None:
    global _store
    with _lock:
        st = _store or {"fav": [], "subjects": []}
        st["updatedAt"] = _now()
        _write_json(STORE_FILE, st)


# ------------------------------------------------------------------ 元数据
def _png_size(path: Path) -> tuple[int | None, int | None]:
    try:
        with path.open("rb") as fh:
            head = fh.read(26)
        if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
            return None, None
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)
    except Exception:
        return None, None


def _image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        return _png_size(path)


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


def _video_meta(path: Path) -> dict:
    binp = ffprobe_bin()
    if not Path(binp).exists():
        return {}
    cmd = [binp, "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=width,height,r_frame_rate:format=duration,size",
           "-of", "json", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        data = json.loads(out.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        fmt = data.get("format") or {}
        fps = None
        rate = stream.get("r_frame_rate") or ""
        if "/" in rate:
            a, b = rate.split("/", 1)
            try:
                fps = round(float(a) / float(b), 2) if float(b) else None
            except Exception:
                fps = None
        return {"w": stream.get("width"), "h": stream.get("height"),
                "seconds": round(float(fmt.get("duration") or 0), 2) if fmt.get("duration") else None,
                "fps": fps}
    except Exception:
        return {}


def _load_cache() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _read_json(CACHE_FILE, {})
        return _cache


def _probe_cached(path: Path) -> dict:
    cache = _load_cache()
    st = path.stat()
    key = f"{path.name}|{st.st_size}|{int(st.st_mtime)}"
    hit = cache.get(key)
    if isinstance(hit, dict):
        return hit
    if path.suffix.lower() in VIDEO_EXT:
        meta = _video_meta(path)
    else:
        w, h = _image_size(path)
        meta = {"w": w, "h": h}
    meta = {k: v for k, v in meta.items() if v is not None}
    cache[key] = meta
    # 只保留当前存在的 key，避免缓存无限增长
    if len(cache) > MAX_SCAN * 2:
        _cache = {k: v for k, v in cache.items() if k.split("|")[0] and (GENERATED / k.split("|")[0]).exists()}
    return meta


def flush_cache() -> None:
    with _lock:
        if _cache is not None:
            _write_json(CACHE_FILE, _cache)


# ------------------------------------------------------------------ 日志索引
def _log_index() -> dict:
    """file → {prompt, source, kind, extra}。只读最后 5000 行，够覆盖本机产物。"""
    idx: dict[str, dict] = {}
    if GEN_LOG.exists():
        try:
            lines = GEN_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]
            for line in lines:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                f = e.get("file")
                if not f:
                    continue
                idx[f] = {
                    "prompt": (e.get("prompt") or "").strip(),
                    "source": "mock" if e.get("upstream_mode") == "mock" else "real",
                    "kind": "image", "model": e.get("model"),
                    "at": e.get("ts"), "ms": e.get("elapsed_ms"),
                }
        except Exception:
            pass
    if VID_LOG.exists():
        try:
            lines = VID_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]
            for line in lines:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                task = e.get("task")
                if task:
                    f = f"vid-{task}.mp4"
                    idx[f] = {"prompt": (e.get("prompt") or "").strip(),
                              "source": "mock" if "127.0.0.1" in str(e.get("base")) or "mock" in str(task) else "real",
                              "kind": "video", "model": e.get("model"),
                              "at": e.get("ts"), "tier": e.get("tier")}
                f = e.get("file")
                if f:
                    idx[f] = {"prompt": f"本集成片 · 第 {e.get('ep', '?')} 集",
                              "source": "local", "kind": "reel",
                              "at": e.get("ts"), "seconds": e.get("seconds")}
        except Exception:
            pass
    return idx


# ------------------------------------------------------------------ 扫描
def _item(name: str, path: Path, idx: dict, favs: set) -> dict:
    st = path.stat()
    meta = _probe_cached(path)
    ext = path.suffix.lower()
    typ = "video" if ext in VIDEO_EXT else "image"
    log = idx.get(name, {})
    source = log.get("source") or ("tool" if name.startswith("tool-") else "local" if name.startswith("reel-") else "unknown")
    return {
        "id": name, "url": f"/assets/generated/{name}", "type": typ,
        "bytes": st.st_size, "mtime": int(st.st_mtime),
        "at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
        "w": meta.get("w"), "h": meta.get("h"),
        "seconds": meta.get("seconds"), "fps": meta.get("fps"),
        "source": source, "model": log.get("model"),
        "prompt": log.get("prompt") or "", "kind": log.get("kind") or typ,
        "fav": name in favs,
    }


def scan(kind: str = "all", q: str = "", sort: str = "new",
         limit: int = 60, offset: int = 0, fav_only: bool = False) -> dict:
    _ensure()
    store = load_store()
    favs = set(store.get("fav") or [])
    idx = _log_index()
    files: list[Path] = []
    try:
        with os.scandir(GENERATED) as it:
            for ent in it:
                if len(files) >= MAX_SCAN:
                    break
                if not ent.is_file():
                    continue
                if not MEDIA_RE.match(ent.name):
                    continue
                files.append(Path(ent.path))
    except FileNotFoundError:
        pass
    items = []
    for p in files:
        if kind == "image" and p.suffix.lower() not in IMAGE_EXT:
            continue
        if kind == "video" and p.suffix.lower() not in VIDEO_EXT:
            continue
        if fav_only and p.name not in favs:
            continue
        items.append(_item(p.name, p, idx, favs))
    if q:
        needle = q.strip().lower()
        items = [x for x in items if needle in x["id"].lower() or needle in (x["prompt"] or "").lower()]
    if sort == "old":
        items.sort(key=lambda x: x["mtime"])
    elif sort == "big":
        items.sort(key=lambda x: x["bytes"], reverse=True)
    elif sort == "duration":
        items.sort(key=lambda x: (x["seconds"] or 0), reverse=True)
    elif sort == "featured":
        # 「推荐」不是算法排序：真实产物优先，其次非 mock，最后才是 mock / 未知；
        # 同档内按时间倒序。这样首屏不会被 QA 的色条图占满。
        items.sort(key=lambda x: (0 if x["source"] == "real" else 1 if x["source"] != "mock" else 2,
                                  0 if x["type"] == "image" else 1, -x["mtime"]))
    else:
        items.sort(key=lambda x: x["mtime"], reverse=True)
    total = len(items)
    page = items[offset: offset + max(1, min(limit, 200))]
    flush_cache()
    fav_total = sum(1 for n in favs if (GENERATED / n).exists())
    return {
        "ok": True, "total": total, "offset": offset, "limit": limit,
        "items": page,
        "counts": {
            "all": len([p for p in files]),
            "image": len([p for p in files if p.suffix.lower() in IMAGE_EXT]),
            "video": len([p for p in files if p.suffix.lower() in VIDEO_EXT]),
            "fav": fav_total,
        },
        "trash": len(_trash_names()),
    }


# ------------------------------------------------------------------ 收藏
def set_fav(name: str, fav: bool) -> dict:
    n = safe_name(name)
    if not n or not media_path(n):
        return {"ok": False, "error": "非法文件名"}
    store = load_store()
    favs = [x for x in (store.get("fav") or []) if x != n]
    if fav:
        favs.append(n)
    store["fav"] = favs[-500:]
    save_store()
    return {"ok": True, "id": n, "fav": bool(fav)}


# ------------------------------------------------------------------ 回收站
def _trash_index() -> dict:
    return _read_json(TRASH / "index.json", {})


def _trash_names() -> list[str]:
    try:
        return [p.name for p in TRASH.iterdir() if p.is_file() and MEDIA_RE.match(p.name)]
    except FileNotFoundError:
        return []


def trash(names: list[str]) -> dict:
    _ensure()
    if not isinstance(names, list) or not names:
        return {"ok": False, "error": "没有要移入回收站的文件"}
    if len(names) > 50:
        return {"ok": False, "error": "一次最多移入 50 个"}
    moved, skipped = [], []
    idx = _trash_index()
    for raw in names:
        n = safe_name(raw)
        if not n:
            skipped.append({"id": str(raw), "why": "非法文件名"})
            continue
        src = media_path(n)
        if not src or not src.exists():
            skipped.append({"id": n, "why": "文件不存在"})
            continue
        dst_name = n
        if (TRASH / dst_name).exists():
            dst_name = f"{src.stem}-{int(time.time())}{src.suffix}"
        try:
            shutil.move(str(src), str(TRASH / dst_name))
        except Exception as exc:
            skipped.append({"id": n, "why": f"移动失败：{exc}"})
            continue
        idx[dst_name] = {"original": n, "at": _now()}
        moved.append(n)
    _write_json(TRASH / "index.json", idx)
    store = load_store()
    store["fav"] = [x for x in (store.get("fav") or []) if x not in moved]
    save_store()
    return {"ok": True, "moved": moved, "skipped": skipped, "trash": len(_trash_names())}


def list_trash() -> dict:
    _ensure()
    idx = _trash_index()
    items = []
    for name in sorted(_trash_names(), key=lambda n: (TRASH / n).stat().st_mtime, reverse=True):
        p = TRASH / name
        st = p.stat()
        # 回收站列表不探测尺寸/时长：那里只显示文件名与恢复按钮，
        # 为了 100+ 个文件逐个 fork ffprobe 会把接口拖到十几秒。
        items.append({"id": name, "original": (idx.get(name) or {}).get("original") or name,
                      "url": f"/assets/_trash/{name}", "bytes": st.st_size,
                      "type": "video" if p.suffix.lower() in VIDEO_EXT else "image",
                      "at": (idx.get(name) or {}).get("at")})
    return {"ok": True, "items": items, "total": len(items)}


def restore(names: list[str]) -> dict:
    _ensure()
    if not isinstance(names, list) or not names:
        return {"ok": False, "error": "没有要恢复的文件"}
    if len(names) > 50:
        return {"ok": False, "error": "一次最多恢复 50 个"}
    idx = _trash_index()
    done, skipped = [], []
    for raw in names:
        n = safe_name(raw)
        if not n:
            skipped.append({"id": str(raw), "why": "非法文件名"})
            continue
        src = media_path(n, TRASH)
        if not src or not src.exists():
            skipped.append({"id": n, "why": "回收站中没有该文件"})
            continue
        original = (idx.get(n) or {}).get("original") or n
        target = media_path(original)
        if target is None:
            skipped.append({"id": n, "why": "原始文件名非法，拒绝恢复"})
            continue
        if target.exists():
            target = GENERATED / f"{target.stem}-restored-{int(time.time())}{target.suffix}"
        try:
            shutil.move(str(src), str(target))
        except Exception as exc:
            skipped.append({"id": n, "why": f"恢复失败：{exc}"})
            continue
        idx.pop(n, None)
        done.append(target.name)
    _write_json(TRASH / "index.json", idx)
    return {"ok": True, "restored": done, "skipped": skipped, "trash": len(_trash_names())}


# ------------------------------------------------------------------ 主体库
def _subject_dirs() -> dict:
    return {p.name: p for p in SUBJECTS.iterdir() if p.is_dir()} if SUBJECTS.exists() else {}


def list_subjects() -> dict:
    _ensure()
    store = load_store()
    items = []
    for s in store.get("subjects") or []:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        refs = [r for r in (s.get("refs") or []) if (SUBJECTS / s["id"] / Path(str(r)).name).exists()]
        cover = s.get("cover") if s.get("cover") and (SUBJECTS / s["id"] / Path(str(s["cover"])).name).exists() else (refs[0] if refs else None)
        items.append({"id": s["id"], "name": s.get("name") or "未命名主体",
                      "note": s.get("note") or "", "createdAt": s.get("createdAt"),
                      "cover": f"/assets/subjects/{s['id']}/{cover}" if cover else None,
                      "refs": [f"/assets/subjects/{s['id']}/{Path(str(r)).name}" for r in refs],
                      "refCount": len(refs)})
    items.sort(key=lambda x: x.get("createdAt") or "", reverse=True)
    trash = [{"id": s.get("id"), "name": s.get("name") or "未命名主体",
              "refCount": len(s.get("refs") or []), "deletedAt": s.get("deletedAt"),
              "trashDir": s.get("trashDir")}
             for s in (store.get("subjectsTrash") or []) if isinstance(s, dict)]
    return {"ok": True, "items": items, "total": len(items), "trash": trash}


def save_subject(payload: dict) -> dict:
    """新建或更新主体。images 是 data URI 列表（最多 9 张，单张 ≤6MB）。

    关键：**只有全部校验通过后才写 store**。校验中途失败不能把半成品条目留在
    内存缓存里 —— 否则下一次 save_store 会把「未命名、0 张图」的幽灵主体写进磁盘。
    """
    _ensure()
    if not isinstance(payload, dict):
        return {"ok": False, "error": "body 必须是对象"}
    name = str(payload.get("name") or "").strip()
    note = str(payload.get("note") or "").strip()[:200]
    if not name:
        return {"ok": False, "error": "请填写主体名称"}
    if len(name) > 40:
        return {"ok": False, "error": "名称最长 40 字"}
    sid = str(payload.get("id") or "").strip()
    if sid and not re.match(r"^[a-z0-9]{6,20}$", sid):
        return {"ok": False, "error": "主体 id 非法"}
    images = payload.get("images") or []
    if not isinstance(images, list):
        return {"ok": False, "error": "images 必须是数组"}
    if len(images) > 9:
        return {"ok": False, "error": "一个主体最多 9 张参考图"}

    with _subject_lock:
        store = load_store()
        subjects = list(store.get("subjects") or [])
        existing = next((x for x in subjects if x.get("id") == sid), None) if sid else None
        if sid and existing is None:
            return {"ok": False, "error": "主体不存在"}
        new = existing is None
        if new:
            sid = uuid.uuid4().hex[:12]
            entry = {"id": sid, "refs": [], "createdAt": _now()}
        else:
            entry = dict(existing)

        d = SUBJECTS / sid
        created_dir = not d.exists()
        d.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []

        def fail(msg: str) -> dict:
            for fn in saved:
                try:
                    (d / fn).unlink(missing_ok=True)
                except Exception:
                    pass
            if created_dir:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass
            return {"ok": False, "error": msg}

        for i, uri in enumerate(images):
            if not isinstance(uri, str):
                continue
            m = re.match(r"^data:(image/(png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$", uri)
            if not m:
                return fail("参考图必须是 png/jpeg/webp 的 data URI")
            try:
                raw = base64.b64decode(re.sub(r"\s+", "", m.group(3)))
            except Exception:
                return fail("参考图 base64 解码失败")
            if len(raw) < 32 or len(raw) > 6 * 1024 * 1024:
                return fail("参考图体积非法（需 <6MB）")
            ext = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}[m.group(2)]
            fn = f"ref-{int(time.time() * 1000)}-{i}{ext}"
            (d / fn).write_bytes(raw)
            saved.append(fn)
            try:
                from PIL import Image
                with Image.open(d / fn) as im:
                    im = im.convert("RGB")
                    im.thumbnail((512, 512))
                    im.save(d / "cover.jpg", "JPEG", quality=86)
            except Exception:
                pass

        entry["refs"] = list(entry.get("refs") or []) + saved
        entry["name"] = name
        entry["note"] = note
        entry["updatedAt"] = _now()
        if (d / "cover.jpg").exists():
            entry["cover"] = "cover.jpg"
        elif entry.get("refs"):
            entry["cover"] = entry["refs"][0]
        if new:
            subjects.insert(0, entry)
        else:
            subjects = [entry if x.get("id") == sid else x for x in subjects]
        store["subjects"] = subjects[:200]
        save_store()
    return {"ok": True, "subject": next((x for x in list_subjects()["items"] if x["id"] == sid), None)}


def delete_subject(sid: str) -> dict:
    """从主体库移除；参考图移入回收站（可恢复），不直接删。"""
    if not isinstance(sid, str) or not re.match(r"^[a-z0-9]{6,20}$", sid):
        return {"ok": False, "error": "主体 id 非法"}
    store = load_store()
    subjects = store.get("subjects") or []
    entry = next((s for s in subjects if s.get("id") == sid), None)
    if not entry:
        return {"ok": False, "error": "主体不存在"}
    d = SUBJECTS / sid
    moved = 0
    trash_dir = None
    if d.exists():
        _ensure()
        dst = TRASH / f"subject-{sid}-{int(time.time())}"
        try:
            shutil.move(str(d), str(dst))
            moved = 1
            trash_dir = dst.name
        except Exception as exc:
            return {"ok": False, "error": f"移入回收站失败：{exc}"}
    store["subjects"] = [s for s in subjects if s.get("id") != sid]
    entry = dict(entry)
    entry["deletedAt"] = _now()
    entry["trashDir"] = trash_dir
    trash = [x for x in (store.get("subjectsTrash") or []) if x.get("id") != sid]
    trash.insert(0, entry)
    store["subjectsTrash"] = trash[:100]
    save_store()
    return {"ok": True, "id": sid, "moved": moved}


def restore_subject(sid: str) -> dict:
    """把已删除主体（含参考图目录）整体恢复。"""
    if not isinstance(sid, str) or not re.match(r"^[a-z0-9]{6,20}$", sid):
        return {"ok": False, "error": "主体 id 非法"}
    store = load_store()
    trash = store.get("subjectsTrash") or []
    entry = next((x for x in trash if x.get("id") == sid), None)
    if not entry:
        return {"ok": False, "error": "回收站里没有该主体"}
    src = TRASH / str(entry.get("trashDir") or "")
    if entry.get("trashDir") and src.exists():
        dst = SUBJECTS / sid
        if dst.exists():
            return {"ok": False, "error": "同名主体目录已存在，无法恢复"}
        try:
            shutil.move(str(src), str(dst))
        except Exception as exc:
            return {"ok": False, "error": f"恢复失败：{exc}"}
    entry = {k: v for k, v in entry.items() if k not in ("deletedAt", "trashDir")}
    store["subjects"] = [entry] + [x for x in (store.get("subjects") or []) if x.get("id") != sid]
    store["subjectsTrash"] = [x for x in trash if x.get("id") != sid]
    save_store()
    return {"ok": True, "id": sid}
