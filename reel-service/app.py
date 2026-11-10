#!/usr/bin/env python3
"""reel-service —— 把「多段镜头拼成一条竖屏成片」做成云端能力。

与原型的区别：输入不再是本机路径白名单，而是**可访问的 https 链接**（带 SSRF 防护），
任务改为**异步**（提交即返回 id，客户端轮询），ffmpeg 在容器里跑。

  POST /v1/reel         {segments:[{url,dur?}], options{}, title}  -> 202 {id}
  GET  /v1/reel/{id}                                             -> {status,url,...}
  GET  /v1/healthz                                                -> 存活 + 队列
  GET  /files/<name>                                              -> 成片（生产由 nginx 直出）

环境变量：
  REEL_PORT=8080           监听端口
  REEL_DATA=/data          成片与临时目录
  REEL_KEY=...             非空则要求请求头 X-Reel-Key
  REEL_PUBLIC_BASE=https://reel.1task.cn   返回给客户端的成片前缀
  REEL_ALLOW_HOSTS=aivideomaker.1task.cn   下载源域名白名单（逗号分隔）
  REEL_WORKERS=2           ffmpeg 并发数
  REEL_MAX_SEG=12 / REEL_MAX_TOTAL=180 / REEL_MAX_BYTES=209715200
  REEL_TTL=86400           成片保留秒数
  REEL_FONT="Noto Sans CJK SC"
  REEL_DEV=0               1=允许 http 与回环地址（只给本机自测用）
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------- 配置
PORT = int(os.environ.get("REEL_PORT", "8080"))
DATA = Path(os.environ.get("REEL_DATA", "/data"))
OUT_DIR = DATA / "out"
WORK_ROOT = DATA / "work"
UPLOAD_DIR = DATA / "uploads"
REEL_KEY = os.environ.get("REEL_KEY", "").strip()
PUBLIC_BASE = os.environ.get("REEL_PUBLIC_BASE", "https://reel.1task.cn").rstrip("/")
ALLOW_HOSTS = {h.strip().lower() for h in
               os.environ.get("REEL_ALLOW_HOSTS", "aivideomaker.1task.cn").split(",") if h.strip()}
WORKERS = max(1, int(os.environ.get("REEL_WORKERS", "2")))
MAX_SEG = int(os.environ.get("REEL_MAX_SEG", "12"))
MAX_TOTAL = float(os.environ.get("REEL_MAX_TOTAL", "180"))
MAX_BYTES = int(os.environ.get("REEL_MAX_BYTES", str(200 * 1024 * 1024)))
TTL = int(os.environ.get("REEL_TTL", "86400"))
ASS_FONT = os.environ.get("REEL_FONT", "Noto Sans CJK SC")
DEV = os.environ.get("REEL_DEV", "0").strip() == "1"

REEL_W, REEL_H = 704, 1248
REEL_FPS = 24
FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"
TIMEOUT = 180
JOB_RE = re.compile(r"^[a-z0-9]{6,32}$")

OPTS_DEFAULT = {"transition": 0.0, "subtitles": False, "titles": False, "music": "off"}
TRANSITIONS = {0.0, 0.3, 0.5}
MUSIC = {"off", "drone"}

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_tasks: "queue.Queue[str]" = queue.Queue()
_busy = 0
_busy_lock = threading.Lock()


# ---------------------------------------------------------------- ffmpeg 基元
def _ff(cmd: list[str], timeout: int = TIMEOUT) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "ffmpeg 超时"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        return False, "ffmpeg: " + " / ".join(tail)[:300]
    return True, ""


def probe(path: Path) -> dict | None:
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries",
             "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30).stdout
        data = json.loads(out)
    except Exception:
        return None
    dur = 0.0
    try:
        dur = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    v = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams") or [])
    if not v or dur <= 0:
        return None
    return {"dur": dur, "has_audio": has_audio, "w": v.get("width"), "h": v.get("height")}


def _ass_text(text: str) -> str:
    return (text.replace("\\", "＼").replace("{", "（").replace("}", "）")
            .replace("\n", " ").replace(",", "，"))[:120]


def _ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def write_ass(path: Path, lines: list[str], dur: float) -> None:
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {REEL_W}", f"PlayResY: {REEL_H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{ASS_FONT},44,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,-1,0,0,0,"
        "100,100,0,0,1,3,0,2,48,48,96,1", "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    texts = [_ass_text(x) for x in lines if _ass_text(x)]
    events = []
    if texts:
        span = max(0.8, (dur - 0.4) / len(texts))
        for i, t in enumerate(texts):
            start = 0.2 + i * span
            end = min(dur - 0.2, start + span - 0.1)
            if end > start:
                events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{t}")
    path.write_text("\n".join(head + events) + "\n", encoding="utf-8")


def normalize_opts(raw: object) -> tuple[dict, str]:
    opt = dict(OPTS_DEFAULT)
    if raw is None:
        return opt, ""
    if not isinstance(raw, dict):
        return opt, "options 必须是对象"
    try:
        tr = float(raw.get("transition", 0) or 0)
    except (TypeError, ValueError):
        return opt, "transition 必须是数字"
    if tr not in TRANSITIONS:
        return opt, f"transition 只支持 {sorted(TRANSITIONS)}"
    opt["transition"] = tr
    opt["subtitles"] = bool(raw.get("subtitles"))
    opt["titles"] = bool(raw.get("titles"))
    music = str(raw.get("music") or "off").lower()
    if music not in MUSIC:
        return opt, f"music 只支持 {sorted(MUSIC)}"
    opt["music"] = music
    return opt, ""


def _segment(work: Path, idx: int, path: Path, info: dict, line: str, opt: dict):
    out = work / f"n{idx}.mp4"
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path)]
    if not info["has_audio"]:
        args += ["-f", "lavfi", "-t", f"{info['dur']:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    vf = (f"scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=decrease,"
          f"pad={REEL_W}:{REEL_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
          f"fps={REEL_FPS},format=yuv420p")
    if opt["subtitles"] and line:
        ass = work / f"n{idx}.ass"
        write_ass(ass, [line], info["dur"])
        vf += f",ass={ass}"
    args += ["-vf", vf, "-t", f"{info['dur']:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(out)]
    ok, err = _ff(args)
    return (out if ok else None), err


def _card(work: Path, name: str, dur: float, title: str, sub: str):
    out = work / name
    t1, t2 = _ass_text(title), _ass_text(sub)
    vf = (f"drawtext=font='{ASS_FONT}':text='{t1}':fontcolor=white:fontsize=44:"
          f"x=(w-text_w)/2:y=(h-text_h)/2-40:alpha='if(lt(t,0.3),t/0.3,1)',"
          f"drawtext=font='{ASS_FONT}':text='{t2}':fontcolor=0xBBBBBB:fontsize=26:"
          f"x=(w-text_w)/2:y=(h-text_h)/2+40:alpha='if(lt(t,0.5),t/0.5,1)'")
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s={REEL_W}x{REEL_H}:r={REEL_FPS}:d={dur}",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-vf", vf, "-t", f"{dur}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(out)]
    ok, err = _ff(args, 60)
    return (out if ok else None), err


def _xfade(work: Path, clips: list[Path], durs: list[float], t: float):
    out = work / "joined.mp4"
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    for c in clips:
        args += ["-i", str(c)]
    parts, vprev, aprev, acc = [], "[0:v]", "[0:a]", durs[0]
    for k in range(1, len(clips)):
        parts.append(f"{vprev}[{k}:v]xfade=transition=fade:duration={t}:offset={acc - t:.3f}[v{k}]")
        parts.append(f"{aprev}[{k}:a]acrossfade=d={t}:c1=tri:c2=tri[a{k}]")
        vprev, aprev = f"[v{k}]", f"[a{k}]"
        acc = acc + durs[k] - t
    args += ["-filter_complex", ";".join(parts), "-map", vprev, "-map", aprev,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(out)]
    ok, err = _ff(args)
    return (out if ok else None), err


def _concat(work: Path, clips: list[Path]):
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    out = work / "joined.mp4"
    ok, err = _ff([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
                   "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return (out if ok else None), err


def _music(work: Path, clip: Path, total: float):
    out = work / "final.mp4"
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip),
            "-f", "lavfi", "-t", f"{total:.3f}", "-i", "sine=frequency=110:sample_rate=44100",
            "-filter_complex",
            "[1:a]lowpass=f=500,tremolo=f=0.15:d=0.5,volume=0.10[pad];"
            "[0:a][pad]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(out)]
    ok, err = _ff(args)
    return (out if ok else None), err


# ---------------------------------------------------------------- 下载（SSRF 防护）
def _host_allowed(host: str) -> tuple[bool, str]:
    host = (host or "").lower()
    if not host:
        return False, "缺少主机名"
    if host in ALLOW_HOSTS:
        return True, ""
    if DEV and host in {"127.0.0.1", "localhost"}:
        return True, ""
    return False, f"域名不在白名单：{host}"


def _ip_safe(host: str, port: int) -> tuple[bool, str]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return False, f"域名解析失败：{exc}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            if DEV and (ip.is_loopback or ip.is_private):
                continue
            return False, f"目标解析到内网地址：{ip}"
    return True, ""


def fetch(url: str, dest: Path) -> tuple[Path | None, str]:
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return None, "url 解析失败"
    if u.scheme not in ("http", "https"):
        return None, "只接受 http(s) 链接"
    if not DEV and u.scheme != "https":
        return None, "线上只接受 https"
    if u.username or u.password:
        return None, "url 不能带凭据"
    host = u.hostname or ""
    ok, err = _host_allowed(host)
    if not ok:
        return None, err
    port = u.port or (443 if u.scheme == "https" else 80)
    ok, err = _ip_safe(host, port)
    if not ok:
        return None, err
    req = urllib.request.Request(url, headers={"User-Agent": "reel-service/1.0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=60) as resp:
            size = int(resp.headers.get("Content-Length") or 0)
            if size > MAX_BYTES:
                return None, f"单段超过上限（{size // (1024 * 1024)}MB > {MAX_BYTES // (1024 * 1024)}MB）"
            got = 0
            with dest.open("wb") as fh:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > MAX_BYTES:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        return None, f"单段超过上限 {MAX_BYTES // (1024 * 1024)}MB"
                    fh.write(chunk)
    except urllib.error.HTTPError as exc:
        return None, f"下载失败 HTTP {exc.code}"
    except Exception as exc:
        return None, f"下载失败：{type(exc).__name__} {exc}"
    if dest.stat().st_size < 1024:
        return None, "下载内容过小"
    return dest, ""


# ---------------------------------------------------------------- 任务
def _set(job_id: str, **kw) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job.update(kw)
            job["updated"] = time.time()


def _run_job(job_id: str) -> None:
    global _busy
    with _jobs_lock:
        job = dict(_jobs.get(job_id) or {})
    if not job:
        return
    work = WORK_ROOT / job_id
    started = time.monotonic()
    try:
        work.mkdir(parents=True, exist_ok=True)
        segs = job.get("_segments") or []
        opt = job["options"]
        _set(job_id, status="running", progress=2, stage="下载素材")
        paths, infos = [], []
        for i, seg in enumerate(segs):
            dest = work / f"src{i}.mp4"
            if seg.get("upload"):
                src = UPLOAD_DIR / seg["upload"]
                if not src.exists() or src.stat().st_size < 1024:
                    raise RuntimeError(f"第 {i + 1} 段：上传件已过期，请重传")
                shutil.copyfile(src, dest)
            else:
                ok, err = fetch(seg["url"], dest)
                if not ok:
                    raise RuntimeError(f"第 {i + 1} 段：{err}")
            info = probe(dest)
            if not info:
                raise RuntimeError(f"第 {i + 1} 段不是可解码的视频")
            paths.append(dest)
            infos.append(info)
            _set(job_id, progress=5 + int(35 * (i + 1) / len(segs)),
                 stage=f"下载素材 {i + 1}/{len(segs)}")

        total_src = sum(x["dur"] for x in infos)
        if total_src > MAX_TOTAL:
            raise RuntimeError(f"总时长 {total_src:.1f}s 超过上限 {MAX_TOTAL:.0f}s")

        _set(job_id, progress=45, stage="统一规格")
        clips, durs = [], []
        if opt["titles"]:
            head, err = _card(work, "head.mp4", 1.8, job.get("title") or "未命名短剧", "AI 生成")
            if not head:
                raise RuntimeError("片头生成失败: " + err)
            clips.append(head)
            durs.append(1.8)
        lines = job.get("lines") or []
        for i, (path, info) in enumerate(zip(paths, infos)):
            line = lines[i] if i < len(lines) else ""
            seg, err = _segment(work, i, path, info, line, opt)
            if not seg:
                raise RuntimeError(f"第 {i + 1} 段处理失败: {err}")
            n = probe(seg) or {}
            clips.append(seg)
            durs.append(float(n.get("dur") or info["dur"]))
            _set(job_id, progress=45 + int(30 * (i + 1) / len(paths)),
                 stage=f"统一规格 {i + 1}/{len(paths)}")
        if opt["titles"]:
            tail, err = _card(work, "tail.mp4", 1.8, "本片由 AI 生成",
                              "分镜图 flux-3-image · 视频 minimaxH3")
            if not tail:
                raise RuntimeError("片尾生成失败: " + err)
            clips.append(tail)
            durs.append(1.8)

        _set(job_id, progress=80, stage="拼接")
        if opt["transition"] > 0 and len(clips) > 1:
            joined, err = _xfade(work, clips, durs, opt["transition"])
        else:
            joined, err = _concat(work, clips)
        if not joined:
            raise RuntimeError("拼接失败: " + err)
        total = sum(durs) - opt["transition"] * max(0, len(clips) - 1)
        final = joined
        if opt["music"] == "drone":
            _set(job_id, progress=90, stage="混底音")
            mixed, err = _music(work, joined, total)
            if not mixed:
                raise RuntimeError("底音混音失败: " + err)
            final = mixed

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{job_id}.mp4"
        out = OUT_DIR / name
        shutil.copyfile(final, out)
        info = probe(out) or {}
        _set(job_id, status="succeeded", progress=100, stage="完成",
             url=f"{PUBLIC_BASE}/files/{name}", seconds=round(info.get("dur") or 0, 2),
             bytes=out.stat().st_size,
             elapsed_ms=int((time.monotonic() - started) * 1000))
    except Exception as exc:
        _set(job_id, status="failed", stage="失败", error=str(exc)[:300],
             elapsed_ms=int((time.monotonic() - started) * 1000))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        with _busy_lock:
            _busy -= 1


def _worker() -> None:
    global _busy
    while True:
        job_id = _tasks.get()
        if job_id is None:
            return
        with _busy_lock:
            _busy += 1
        try:
            _run_job(job_id)
        finally:
            _tasks.task_done()


def _cleanup() -> None:
    while True:
        time.sleep(600)
        now = time.time()
        try:
            for f in OUT_DIR.glob("*.mp4"):
                if now - f.stat().st_mtime > TTL:
                    f.unlink(missing_ok=True)
            for u in UPLOAD_DIR.glob("*.mp4"):
                if now - u.stat().st_mtime > 7200:
                    u.unlink(missing_ok=True)
            for w in WORK_ROOT.glob("*"):
                if now - w.stat().st_mtime > 3600:
                    shutil.rmtree(w, ignore_errors=True)
        except OSError:
            pass
        with _jobs_lock:
            for k, v in list(_jobs.items()):
                if now - v.get("updated", now) > TTL:
                    _jobs.pop(k, None)


# ---------------------------------------------------------------- HTTP
def _ffmpeg_version() -> str:
    try:
        out = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True, timeout=10).stdout
        return (out.splitlines() or [""])[0][:80]
    except Exception:
        return "unknown"


FFMPEG_VERSION = _ffmpeg_version()


class Handler(BaseHTTPRequestHandler):
    server_version = "reel-service/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 只留访问日志的最小信息
        print(f"[reel] {self.address_string()} {fmt % args}", flush=True)

    def _json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        if not REEL_KEY:
            return True
        return self.headers.get("X-Reel-Key", "") == REEL_KEY

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/v1/healthz":
            with _jobs_lock:
                counts = {}
                for j in _jobs.values():
                    counts[j["status"]] = counts.get(j["status"], 0) + 1
            return self._json({
                "ok": True, "service": "reel-service", "ffmpeg": FFMPEG_VERSION,
                "font": ASS_FONT, "workers": WORKERS, "busy": _busy,
                "queued": _tasks.qsize(), "jobs": counts,
                "uploads": True,
                "limits": {"maxSegments": MAX_SEG, "maxTotalSeconds": MAX_TOTAL,
                           "maxBytes": MAX_BYTES, "ttl": TTL,
                           "size": [REEL_W, REEL_H], "fps": REEL_FPS},
            })
        if path.startswith("/v1/reel/"):
            job_id = path[len("/v1/reel/"):].strip("/")
            if not JOB_RE.match(job_id):
                return self._json({"ok": False, "error": "id 不合法"}, 400)
            with _jobs_lock:
                job = dict(_jobs.get(job_id) or {})
            if not job:
                return self._json({"ok": False, "error": "任务不存在或已过期"}, 404)
            return self._json({"ok": True, **{k: job.get(k) for k in
                    ("id", "status", "stage", "progress", "url", "seconds", "bytes",
                     "segments", "elapsed_ms", "error") if k in job}})
        if path.startswith("/files/") and DEV:
            name = path[len("/files/"):]
            if not re.match(r"^[a-z0-9]{6,32}\.mp4$", name):
                return self._json({"ok": False, "error": "not found"}, 404)
            f = OUT_DIR / name
            if not f.exists():
                return self._json({"ok": False, "error": "not found"}, 404)
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        return self._json({"ok": False, "error": "not found"}, 404)

    def do_PUT(self) -> None:
        """直传一段素材：PUT /v1/blob/<uuid>.mp4，body 就是文件字节。

        为什么要它：本机的镜头视频不在公网上，云端没法按 URL 拉 —— 直传是第一公民。
        """
        path = self.path.split("?", 1)[0]
        if not path.startswith("/v1/blob/"):
            return self._json({"ok": False, "error": "not found"}, 404)
        if not self._authed():
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        name = path[len("/v1/blob/"):]
        if not re.match(r"^[a-z0-9]{8,40}\.mp4$", name):
            return self._json({"ok": False, "error": "name 只允许小写字母数字 + .mp4"}, 400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return self._json({"ok": False, "error": "空请求体"}, 400)
        if length > MAX_BYTES:
            return self._json({"ok": False,
                               "error": f"超过单段上限 {MAX_BYTES // (1024 * 1024)}MB"}, 413)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_DIR / name
        remain = length
        with dest.open("wb") as fh:
            while remain > 0:
                chunk = self.rfile.read(min(1024 * 256, remain))
                if not chunk:
                    break
                fh.write(chunk)
                remain -= len(chunk)
        got = dest.stat().st_size
        if got != length or got < 1024:
            dest.unlink(missing_ok=True)
            return self._json({"ok": False, "error": f"上传不完整（{got}/{length}）"}, 400)
        return self._json({"ok": True, "upload": name, "bytes": got}, 201)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/v1/reel":
            return self._json({"ok": False, "error": "not found"}, 404)
        if not self._authed():
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 512 * 1024:
            return self._json({"ok": False, "error": "请求体为空或过大"}, 400)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
        if not isinstance(body, dict):
            return self._json({"ok": False, "error": "body 必须是对象"}, 400)
        segs_raw = body.get("segments")
        if not isinstance(segs_raw, list) or not segs_raw:
            return self._json({"ok": False, "error": "segments 不能为空"}, 400)
        if len(segs_raw) > MAX_SEG:
            return self._json({"ok": False, "error": f"最多 {MAX_SEG} 段"}, 400)
        segs = []
        for i, s in enumerate(segs_raw):
            if not isinstance(s, dict):
                return self._json({"ok": False, "error": f"第 {i + 1} 段格式不对"}, 400)
            if isinstance(s.get("upload"), str) and s["upload"]:
                if not re.match(r"^[a-z0-9]{8,40}\.mp4$", s["upload"]):
                    return self._json({"ok": False, "error": f"第 {i + 1} 段 upload id 不合法"}, 400)
                if not (UPLOAD_DIR / s["upload"]).exists():
                    return self._json({"ok": False, "error": f"第 {i + 1} 段 upload 不存在或已过期"}, 400)
                segs.append({"upload": s["upload"]})
                continue
            if not isinstance(s.get("url"), str):
                return self._json({"ok": False, "error": f"第 {i + 1} 段需要 url 或 upload"}, 400)
            u = urllib.parse.urlparse(s["url"])
            if u.scheme not in ("http", "https") or (not DEV and u.scheme != "https"):
                return self._json({"ok": False,
                                   "error": f"第 {i + 1} 段只接受 {'http(s)' if DEV else 'https'} 链接"}, 400)
            ok, err = _host_allowed(u.hostname or "")
            if not ok:
                return self._json({"ok": False, "error": f"第 {i + 1} 段{err}"}, 400)
            segs.append({"url": s["url"]})
        opt, err = normalize_opts(body.get("options"))
        if err:
            return self._json({"ok": False, "error": err}, 400)
        lines = body.get("lines")
        lines = [str(x)[:120] for x in lines][:MAX_SEG] if isinstance(lines, list) else []
        job_id = base64.b32encode(hashlib.sha256(
            f"{time.time()}{id(body)}".encode()).digest()).decode().lower()[:12]
        job_id = re.sub(r"[^a-z0-9]", "0", job_id)
        with _jobs_lock:
            _jobs[job_id] = {
                "id": job_id, "status": "queued", "stage": "排队", "progress": 0,
                "segments": len(segs), "options": opt, "title": str(body.get("title") or "")[:60],
                "lines": lines, "at": time.time(), "updated": time.time(),
                "url": None, "error": None, "_segments": segs,
            }
        _tasks.put(job_id)
        return self._json({"ok": True, "id": job_id, "status": "queued"}, 202)


def main() -> int:
    for d in (OUT_DIR, WORK_ROOT, UPLOAD_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for _ in range(WORKERS):
        threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_cleanup, daemon=True).start()
    print(f"[reel] listening :{PORT} workers={WORKERS} data={DATA} public={PUBLIC_BASE} "
          f"allow={sorted(ALLOW_HOSTS)} dev={DEV} ffmpeg={FFMPEG_VERSION}", flush=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
