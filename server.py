#!/usr/bin/env python3
"""即梦复刻页的本地代理 + 静态服务。

职责：
  1. 托管静态页面（index.html / styles.css / app.js / assets/）。
  2. POST /api/generate 转发到自建 bfl2openai 网关的 flux-3-image，
     把 b64_json 落成 PNG 文件后返回本地 URL。**密钥只存在于本进程**。
  3. GET /api/status、/api/logs 供页面与自检读取。

安全与计费约束（硬性）：
  - 仅监听 127.0.0.1；不向浏览器暴露任何上游凭据。
  - 真实生成每次固定 n=1、size=1024x1024（1k 表内档），不转发前端的多张请求。
  - 单进程真实调用次数上限 MAX_CALLS；同一时刻只允许 1 个真实生成。
  - 上游失败如实返回，不静默重试、不静默降级为演示结果。
  - 所有真实调用写 qa/generate-log.jsonl，便于事后核对计费次数。

用法：
  python3 server.py                 # 真实模式（需 .env 里有 BFL2OPENAI_API_KEY）
  JIMENG_REAL=0 python3 server.py   # 强制演示模式，零出站请求
  python3 server.py --selftest      # 只跑纯函数单测，零网络
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:                       # 首帧裁切要用；没装就退回原图，不阻断视频链路
    from PIL import Image
except Exception:          # pragma: no cover
    Image = None

import billing              # 积分账本 + new-api 令牌额度桥接（只读查额度，不发起生成）
import media_lib            # 本机媒体库：浏览 / 收藏 / 回收站 / 主体
import media_tools          # 本地确定性图像视频工具（不调模型）
from urllib.parse import urlparse, parse_qs, urlencode

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
GENERATED = ASSETS / "generated"
QA = ROOT / "qa"
LOG_FILE = QA / "generate-log.jsonl"

DEFAULT_KEY_FILE = Path.home() / "PycharmProjects/AI/reverse/bfl/bfl2openai/.env"
DEFAULT_BASE = "https://bfl.1task.cn"
DEFAULT_MODEL = os.environ.get("BFL_MODEL", "flux-3-image")
MODEL = DEFAULT_MODEL                 # 当前默认模型，refresh_config() 会按页面配置重算
MAX_MODELS = 20                       # 快捷配置 models 的上限
PORT = int(os.environ.get("PORT", "8788"))
# 容器内需要 0.0.0.0；本机默认仍只绑回环，避免把开发代理裸到局域网
HOST = os.environ.get("JIMENG_HOST", "127.0.0.1").strip() or "127.0.0.1"
# 公网部署时允许的站点 Host（逗号分隔）。默认空 = 只认本机同源。
PUBLIC_HOSTS = {x.strip().lower() for x in os.environ.get("JIMENG_PUBLIC_HOSTS", "").split(",") if x.strip()}
MAX_CALLS = int(os.environ.get("JIMENG_MAX_CALLS", "8"))

# ---------------- 积分计费 ----------------
# 计费口径：1 积分 = 0.01 美元 = 5000 new-api quota（1 USD = 100 积分）。
# 单张 1K 出图的积分 = 上游 /api/pricing 的按次价 × 100；取不到才用默认 0.038 USD。
# 上游额度（new-api 的 /v1/dashboard/billing/*）是资金来源；本地 sqlite 账本负责
# 「提交冻结 → 成功结算 → 失败释放」，两者用同一个 key 关联。上游没有 billing 端点时
# 自动退化为本地账本（余额持久化），不会假装读到了上游余额。
BILLING_ENABLED = os.environ.get("JIMENG_BILLING", "1").strip() != "0"
BILLING_MODE = os.environ.get("JIMENG_BILLING_MODE", "auto").strip().lower()
if BILLING_MODE not in ("auto", "upstream", "local"):
    BILLING_MODE = "auto"
IMAGE_CREDITS_ENV = os.environ.get("JIMENG_IMAGE_CREDITS", "").strip()
DEFAULT_IMAGE_USD = float(os.environ.get("JIMENG_IMAGE_USD", "0.038") or 0.038)
try:
    IMAGE_CREDITS = (float(IMAGE_CREDITS_ENV) if IMAGE_CREDITS_ENV
                     else round(DEFAULT_IMAGE_USD * billing.CREDITS_PER_USD, 2))
except ValueError:
    IMAGE_CREDITS_ENV = ""
    IMAGE_CREDITS = round(DEFAULT_IMAGE_USD * billing.CREDITS_PER_USD, 2)
IMAGE_PRICE_USD = DEFAULT_IMAGE_USD
IMAGE_PRICE_SOURCE = "env" if IMAGE_CREDITS_ENV else "default"
BILLING = billing.Ledger()
REAL = os.environ.get("JIMENG_REAL", "1") != "0"
TIMEOUT = int(os.environ.get("BFL_TIMEOUT", "150"))
TRACE_FILE = QA / "trace.jsonl"
# 测试可用 JIMENG_CONFIG_FILE 指向临时文件，避免污染常驻实例的配置
RT_CONFIG_FILE = Path(os.environ.get("JIMENG_CONFIG_FILE", str(QA / "runtime-config.json")))

# 配置优先级（高 → 低）：环境变量 > 页面配置 > 本机默认 .env
#   base: BFL_BASE          > runtime-config.base > DEFAULT_BASE
#   key : BFL2OPENAI_API_KEY > BFL_KEY_FILE(指向的文件) > runtime-config.apiKey > 默认 .env
ENV_BASE = os.environ.get("BFL_BASE", "").strip()
ENV_KEY = os.environ.get("BFL2OPENAI_API_KEY", "").strip()
# 多模型：逗号分隔，首个为默认模型；页面配置（runtime-config.json）优先级更高
ENV_MODELS_RAW = os.environ.get("JIMENG_MODELS", "").strip()
ENV_KEY_FILE = os.environ.get("BFL_KEY_FILE", "").strip()

# 下面这些是「当前生效值」，由 refresh_config() 在启动与保存配置后重算。
BASE = DEFAULT_BASE
API_KEY = ""
MOCK_MODE = False
UPSTREAM_MODE = "live"
REAL_READY = False
CONFIG: dict = {}

# 明确规则：真实生成只允许表内最小档，避免表外尺寸组合导致上游 token 作废。
FIXED_SIZE = "1024x1024"
# 参考图约束（与上游契约一致：最多 10 张；单张/总量给自己再收紧一档）
MAX_REFS = 10
MAX_REF_BYTES = 6 * 1024 * 1024
MAX_REFS_TOTAL = 24 * 1024 * 1024
REF_MIMES = {"image/png", "image/jpeg", "image/webp"}
DATA_URI_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)$")
FIXED_RESOLUTION = "1k"
MAX_PROMPT = 2000

# ---------------- 视频（aivideomaker H3 · 方舟 Seedance 协议） ----------------
# 线上服务：AVM_BASE + /api/v3/contents/generations/tasks；鉴权是透传网页会话 cookie。
# 免费档只认「turbo + ≤10s」：720p/8s 与 480p/10s 实测 paid=false ⇒ 这里钉 720p/8s。
AVM_BASE = os.environ.get("AVM_BASE", "https://aivideomaker.1task.cn").rstrip("/")
AVM_ENV_COOKIE = os.environ.get("AVM_COOKIE", "").strip()
AVM_COOKIE_FILE = Path(os.environ.get("AVM_COOKIE_FILE", str(QA / "avm-cookie.txt")))
AVM_MODEL = os.environ.get("AVM_MODEL", "minimaxH3")
AVM_RESOLUTION = os.environ.get("AVM_RESOLUTION", "720p")
AVM_DURATION = int(os.environ.get("AVM_DURATION", "8"))
AVM_TIMEOUT = int(os.environ.get("AVM_TIMEOUT", "180"))
MAX_VIDEOS = int(os.environ.get("JIMENG_MAX_VIDEOS", "12"))
# 视频档位：免费档 720p/8s（实测 paid=false）；计费档默认**关闭**，要服务端显式打开
# AVM_ALLOW_PAID=1 才会接受 —— 避免一次误点就花上游额度。
AVM_ALLOW_PAID = os.environ.get("AVM_ALLOW_PAID", "0").strip() == "1"
VIDEO_TIERS = {"free": ("720p", 8), "paid": ("1080p", 10)}
# 无首帧（纯文生视频）时由前端显式指定比例；有首帧一律 adaptive，让上游跟随画面。
VIDEO_RATIOS = {"9:16", "16:9", "1:1", "4:3", "3:4", "21:9", "adaptive"}
# Turnstile 铸造服务（本机常驻）：站点有速度闸门时，提交前补一枚 token。
# 不配也能跑 —— 闸门没翻起来时上游不需要它。
AVM_MINTER_URL = os.environ.get("AVM_MINTER_URL", "http://127.0.0.1:39011").rstrip("/")
AVM_MINTER_KEY = os.environ.get("AVM_MINTER_KEY", "").strip()
AVM_MINTER_TARGET = os.environ.get("AVM_MINTER_TARGET", "aivideomaker")
AVM_MINT = os.environ.get("AVM_MINT", "auto").strip().lower() != "off"
VIDEO_LOG = QA / "video-log.jsonl"
VIDEO_AR = (9, 16)                       # 竖屏首帧：分镜画框本来就按这个比例预览
VIDEO_MAX_FRAME_W = 720
VIDEO_TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
# 整集拼接：只接受本代理自己落盘的成片路径，别把任意路径喂给 ffmpeg
VIDEO_PATH_RE = re.compile(r"^/assets/generated/vid-[A-Za-z0-9._-]{1,80}\.mp4$")
REEL_MAX_SEG = 12
REEL_W, REEL_H = 704, 1248
REEL_FPS = 24
REEL_TIMEOUT = int(os.environ.get("JIMENG_REEL_TIMEOUT", "180"))
# 云端拼接（reel.1task.cn）：本机素材先直传，云端 ffmpeg 拼完再拉回来
REEL_CLOUD_BASE = os.environ.get("REEL_CLOUD_BASE", "https://reel.1task.cn").rstrip("/")
REEL_CLOUD_ENV_KEY = os.environ.get("REEL_CLOUD_KEY", "").strip()
REEL_CLOUD_KEY_FILE = Path(os.environ.get("REEL_CLOUD_KEY_FILE", str(QA / "reel-cloud-key.txt")))
REEL_CLOUD_TIMEOUT = int(os.environ.get("REEL_CLOUD_TIMEOUT", "300"))
FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
_reel_lock = threading.Lock()
_video_lock = threading.Lock()
_video_calls = 0
_video_tasks: dict[str, dict] = {}

# ---------------- 文本模型（loomy2api · OpenAI 兼容 chat） ----------------
# 剧本/分镜文字原先按关键词拼模板；这里接一个真实的 chat 端点。
# 没配 key 时自动退回模板 —— 页面会标明「剧本：本地模板」，不冒充模型输出。
LLM_BASE = os.environ.get("LLM_BASE", "https://loomy.1task.cn/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "DeepSeek-V4.1-Flash").strip()
LLM_ENV_KEY = os.environ.get("LLM_KEY", "").strip()
LLM_KEY_FILE = Path(os.environ.get("LLM_KEY_FILE", str(QA / "llm-key.txt")))
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "240"))
LLM_MODE = os.environ.get("JIMENG_LLM", "auto").strip().lower()      # auto | off
LLM_LOG = QA / "llm-log.jsonl"
LLM_MAX_PROMPT = 500                      # 与页面 textarea 的 maxlength 对齐
# 文本模型的运行时配置（快捷配置链接 / 后续页面配置都写这里）：
# 优先级 页面 > LLM_KEY 环境变量 > qa/llm-key.txt（与图像网关同构）
RT_LLM_FILE = Path(os.environ.get("JIMENG_LLM_CONFIG_FILE", str(QA / "runtime-llm.json")))
LLM_ENV_BASE = LLM_BASE
LLM_ENV_MODEL = LLM_MODEL
LLM_RT: dict = {}
LLM_RT_KEY = ""
LLM_SOURCE = {"base": "env", "key": "none", "model": "env"}
# ---------------- Agent（只做规划，不自动执行） ----------------
# auto = 有 LLM 就真规划；mock = 返回确定性方案（测试用，零出站）；off = 关闭
AGENT_MODE = os.environ.get("JIMENG_AGENT", "auto").strip().lower()
_llm_lock = threading.Lock()

SHOT_SIZES = ["大全景", "全景", "中景", "近景", "特写", "过肩", "俯拍"]


def avm_cookie() -> str:
    if AVM_ENV_COOKIE:
        return AVM_ENV_COOKIE
    try:
        return AVM_COOKIE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def avm_auth_header() -> str:
    v = avm_cookie()
    if not v:
        return ""
    return f"Bearer {v if '=' in v else 'auth_session=' + v}"


def avm_ready() -> bool:
    return bool(avm_cookie())


def crop_first_frame(uri: str, ar: tuple[int, int] = VIDEO_AR,
                     max_w: int = VIDEO_MAX_FRAME_W) -> tuple[str, tuple[int, int] | None]:
    """把分镜图裁成竖屏首帧。

    上游按首帧比例出片（ratio=adaptive），方形图会得到方形视频；而画框本来就是按
    9:16 裁切预览的 ⇒ 这里裁的是「用户看到的同一块画面」，不是另裁一版。
    """
    m = DATA_URI_RE.match(uri or "")
    if not m or Image is None:
        return uri, None
    try:
        img = Image.open(io.BytesIO(base64.b64decode(m.group(2)))).convert("RGB")
    except Exception:
        return uri, None
    w, h = img.size
    target = ar[0] / ar[1]
    if w / h > target * 1.01:
        nw = max(1, round(h * target))
        left = (w - nw) // 2
        img = img.crop((left, 0, left + nw, h))
    elif w / h < target * 0.99:
        nh = max(1, round(w / target))
        top = (h - nh) // 2
        img = img.crop((0, top, w, top + nh))
    if img.width > max_w:
        img = img.resize((max_w, max(1, round(max_w / target))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), img.size


def build_video_payload(prompt: str, image_uri: str | None = None, tier: str = "free",
                        ratio: str = "9:16") -> dict:
    """方舟任务体。首帧是可选的：

    有首帧 → ratio 必须 adaptive，上游按首帧比例出片（分镜画框已是 9:16）；
    没有首帧 → 用前端选的比例，否则纯文生视频会退化成方形。
    """
    resolution, duration = VIDEO_TIERS.get(tier, VIDEO_TIERS["free"])
    content = [{"type": "text", "text": prompt}]
    if image_uri:
        content.append({"type": "image_url", "role": "first_frame",
                        "image_url": {"url": image_uri}})
    return {
        "model": AVM_MODEL,
        "content": content,
        "resolution": resolution,
        "duration": duration,
        "ratio": "adaptive" if image_uri else (ratio if ratio in VIDEO_RATIOS else "9:16"),
    }


def append_video_log(entry: dict) -> None:
    try:
        QA.mkdir(parents=True, exist_ok=True)
        with VIDEO_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def avm_request(method: str, path: str, payload: dict | None = None,
                timeout: int | None = None) -> tuple[dict, int]:
    if not avm_ready():
        return {"error": "视频未配置：缺少 AVM_COOKIE"}, 503
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(AVM_BASE + path, data=data, method=method, headers={
        "Authorization": avm_auth_header(), "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": "jimeng-clone-video/1.0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout or AVM_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")[:600]
        try:
            j = json.loads(raw)
            msg = (j.get("error") or {}).get("message") or raw
        except Exception:
            msg = raw
        return {"error": f"上游 HTTP {exc.code}: {msg}"[:500]}, exc.code
    except Exception as exc:
        return {"error": f"上游不可达: {str(exc)[:200]}"}, 502


def mint_turnstile() -> str:
    """铸一枚 Turnstile token。失败就返回空串 —— 提交照常走，不因为铸造挂了拦住出片。"""
    if not AVM_MINT or not AVM_MINTER_URL:
        return ""
    url = f"{AVM_MINTER_URL}/v1/turnstile/mint?target={AVM_MINTER_TARGET}"
    headers = {"Content-Type": "application/json"}
    if AVM_MINTER_KEY:
        headers["X-Minter-Key"] = AVM_MINTER_KEY
    req = urllib.request.Request(url, data=b"{}", method="POST", headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=150) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return ""
    tokens = data.get("tokens") or []
    if isinstance(tokens, list) and tokens:
        return str(tokens[0])
    return str(data.get("token") or "")


def submit_video(prompt: str, image_uri: str | None = None, tier: str = "free",
                 ratio: str = "9:16") -> tuple[dict, int]:
    global _video_calls
    if not avm_ready():
        return {"ok": False, "error": "视频未配置：缺少 AVM_COOKIE（qa/avm-cookie.txt 或环境变量）"}, 503
    if tier not in VIDEO_TIERS:
        return {"ok": False, "error": "tier 只支持 free|paid"}, 400
    if ratio not in VIDEO_RATIOS:
        return {"ok": False, "error": f"ratio 只支持 {'/'.join(sorted(VIDEO_RATIOS))}"}, 400
    if tier == "paid" and not AVM_ALLOW_PAID:
        return {"ok": False, "error": "计费档未开启：服务端设 AVM_ALLOW_PAID=1 才会接受 1080p/10s"}, 403
    with _video_lock:
        if _video_calls >= MAX_VIDEOS:
            return {"ok": False, "error": f"本进程视频调用已达上限 {MAX_VIDEOS} 次（防重复计费）"}, 429
        _video_calls += 1
        seq = _video_calls
    # 无首帧时 frame 为空串：既不裁切，也不往 content 里塞 image_url
    frame, size = crop_first_frame(image_uri) if image_uri else ("", None)
    payload = build_video_payload(prompt, frame or None, tier, ratio)
    token = mint_turnstile()
    if token:
        payload["extra_body"] = {"aivideomaker_captcha_token": token}
    started = time.monotonic()
    data, status = avm_request("POST", "/api/v3/contents/generations/tasks",
                               payload, timeout=240)
    elapsed = int((time.monotonic() - started) * 1000)
    append_video_log({
        "ts": datetime.now(timezone.utc).isoformat(), "call": seq,
        "status": "ok" if data.get("id") else "error", "http_status": status,
        "elapsed_ms": elapsed, "task": data.get("id"), "prompt": prompt[:120],
        "captcha_token": bool(token),
        "frame": list(size) if size else None, "tier": tier, "ratio": payload["ratio"],
        "resolution": VIDEO_TIERS.get(tier, VIDEO_TIERS["free"])[0],
        "duration": VIDEO_TIERS.get(tier, VIDEO_TIERS["free"])[1],
        "model": AVM_MODEL, "base": AVM_BASE,
        "error": data.get("error"),
    })
    if not data.get("id"):
        return {"ok": False, "error": data.get("error") or f"上游未返回任务 id（HTTP {status}）"},                (status if status >= 400 else 502)
    _video_tasks[data["id"]] = {"created_at": time.time(), "frame": size, "prompt": prompt[:120]}
    res, dur = VIDEO_TIERS.get(tier, VIDEO_TIERS["free"])
    return {"ok": True, "id": data["id"], "status": "queued", "tier": tier,
            "resolution": res, "duration": dur,
            "frame": list(size) if size else None, "call": seq}, 200


def ensure_video_file(task_id: str, url: str) -> str | None:
    if not VIDEO_TASK_RE.match(task_id):
        return None
    name = f"vid-{task_id}.mp4"
    path = GENERATED / name
    if path.exists() and path.stat().st_size > 1024:
        return f"/assets/generated/{name}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "jimeng-clone-video/1.0"})
        with opener.open(req, timeout=240) as resp:
            body = resp.read()
    except Exception:
        return None
    if len(body) < 1024 or body[4:8] != b"ftyp":
        return None
    GENERATED.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.replace(path)
    return f"/assets/generated/{name}"


def video_status(task_id: str) -> tuple[dict, int]:
    if not VIDEO_TASK_RE.match(task_id):
        return {"ok": False, "error": "task id 非法"}, 400
    data, status = avm_request("GET", f"/api/v3/contents/generations/tasks/{task_id}", timeout=40)
    if not data.get("status"):
        # 4xx 是确定的失败（任务不存在/参数错）；5xx 与超时只是这一瞬查不到，
        # 交给前端继续轮询 —— 别把一次网络抖动写成「视频失败」。
        if 400 <= status < 500:
            return {"ok": False, "error": data.get("error") or f"查询失败（HTTP {status}）"}, status
        return {"ok": True, "id": task_id, "status": "unknown",
                "warn": data.get("error") or "上游查询超时"}, 200
    st = data.get("status")
    err = data.get("error")
    if isinstance(err, dict):
        err = err.get("message") or err.get("code") or json.dumps(err, ensure_ascii=False)
    out = {"ok": True, "id": task_id, "status": st, "error": (str(err)[:200] if err else None),
           "seconds": data.get("duration"), "ratio": data.get("ratio"),
           "frame": list(_video_tasks.get(task_id, {}).get("frame") or []) or None}
    if st == "succeeded":
        url = (data.get("content") or {}).get("video_url")
        if not url:
            out["warn"] = "上游成功但没有 video_url"
        else:
            local = ensure_video_file(task_id, url)
            if local:
                out["url"] = local
            else:
                out["warn"] = "成片已生成，但本地转存失败（下次轮询会再试）"
    return out, 200


def probe_media(path: Path) -> dict | None:
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json",
                              "-show_format", "-show_streams", str(path)],
                             capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout or "{}")
    except Exception:
        return None
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        return None
    try:
        dur = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return {"w": int(video.get("width") or 0), "h": int(video.get("height") or 0),
            "dur": dur, "has_audio": any(s.get("codec_type") == "audio" for s in streams)}


REEL_OPTS_DEFAULT = {"transition": 0.0, "subtitles": False, "titles": False, "music": "off"}
REEL_TRANSITIONS = {0.0, 0.3, 0.5}
REEL_MUSIC = {"off", "drone"}
ASS_FONT = os.environ.get("JIMENG_ASS_FONT", "Heiti SC")


def _ts(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    return f"{h}:{m:02d}:{sec % 60:05.2f}"


def _ass_text(text: str) -> str:
    # ASS 里 {} 是特效块、\ 是转义符：台词里出现就把它们换成全角，别让字幕炸掉
    s = str(text or "").replace("\\", "＼").replace("{", "（").replace("}", "）")
    return s.replace("\r", "").replace("\n", "\\N").strip()[:80]


def write_ass(path: Path, lines: list[str], dur: float) -> None:
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {REEL_W}", f"PlayResY: {REEL_H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{ASS_FONT},44,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,-1,0,0,0,"
        "100,100,0,0,1,3,0,2,48,48,96,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    texts = [_ass_text(x) for x in lines if _ass_text(x)]
    events = []
    if texts:
        # 一镜一段台词：按条数把这段时长均分，读起来像分镜里的节奏
        span = max(0.8, (dur - 0.4) / len(texts))
        for i, t in enumerate(texts):
            start = 0.2 + i * span
            end = min(dur - 0.2, start + span - 0.1)
            if end <= start:
                continue
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{t}")
    path.write_text("\n".join(head + events) + "\n", encoding="utf-8")


def normalize_opts(raw: object) -> tuple[dict, str]:
    opt = dict(REEL_OPTS_DEFAULT)
    if raw is None:
        return opt, ""
    if not isinstance(raw, dict):
        return opt, "options 必须是对象"
    try:
        tr = float(raw.get("transition", 0) or 0)
    except (TypeError, ValueError):
        return opt, "transition 必须是数字"
    if tr not in REEL_TRANSITIONS:
        return opt, f"transition 只支持 {sorted(REEL_TRANSITIONS)}"
    opt["transition"] = tr
    opt["subtitles"] = bool(raw.get("subtitles"))
    opt["titles"] = bool(raw.get("titles"))
    music = str(raw.get("music") or "off").lower()
    if music not in REEL_MUSIC:
        return opt, f"music 只支持 {sorted(REEL_MUSIC)}"
    opt["music"] = music
    return opt, ""


def _ff(cmd: list[str], timeout: int) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "ffmpeg 超时"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        return False, "ffmpeg: " + " / ".join(tail)[:300]
    return True, ""


def _segment(work: Path, idx: int, path: Path, info: dict, lines: list[str],
             opt: dict) -> tuple[Path | None, str]:
    out = work / f"n{idx}.mp4"
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path)]
    if not info["has_audio"]:
        args += ["-f", "lavfi", "-t", f"{info['dur']:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    vf = (f"scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=decrease,"
          f"pad={REEL_W}:{REEL_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
          f"fps={REEL_FPS},format=yuv420p")
    if opt["subtitles"] and lines:
        ass = work / f"n{idx}.ass"
        write_ass(ass, lines, info["dur"])
        vf += f",ass={ass}"
    args += ["-vf", vf, "-t", f"{info['dur']:.3f}"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    args += ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(out)]
    ok, err = _ff(args, REEL_TIMEOUT)
    return (out if ok else None), err


def _card(work: Path, name: str, dur: float, title: str, sub: str) -> tuple[Path | None, str]:
    out = work / name
    t1 = _ass_text(title)
    t2 = _ass_text(sub)
    vf = (f"drawtext=font='{ASS_FONT}':text='{t1}':fontcolor=white:fontsize=44:"
          f"x=(w-text_w)/2:y=(h-text_h)/2-40:"
          f"alpha='if(lt(t,0.3),t/0.3,1)',"
          f"drawtext=font='{ASS_FONT}':text='{t2}':fontcolor=0xBBBBBB:fontsize=26:"
          f"x=(w-text_w)/2:y=(h-text_h)/2+40:"
          f"alpha='if(lt(t,0.5),t/0.5,1)'")
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s={REEL_W}x{REEL_H}:r={REEL_FPS}:d={dur}",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
            "-vf", vf, "-t", f"{dur}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(out)]
    ok, err = _ff(args, 60)
    return (out if ok else None), err


def _xfade_chain(work: Path, clips: list[Path], durs: list[float], t: float) -> tuple[Path | None, str]:
    out = work / "joined.mp4"
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    for c in clips:
        args += ["-i", str(c)]
    parts = []
    vprev, aprev = "[0:v]", "[0:a]"
    acc = durs[0]
    for k in range(1, len(clips)):
        off = acc - t
        parts.append(f"{vprev}[{k}:v]xfade=transition=fade:duration={t}:offset={off:.3f}[v{k}]")
        parts.append(f"{aprev}[{k}:a]acrossfade=d={t}:c1=tri:c2=tri[a{k}]")
        vprev, aprev = f"[v{k}]", f"[a{k}]"
        acc = acc + durs[k] - t
    args += ["-filter_complex", ";".join(parts), "-map", vprev, "-map", aprev,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(out)]
    ok, err = _ff(args, REEL_TIMEOUT)
    return (out if ok else None), err


def _concat(work: Path, clips: list[Path]) -> tuple[Path | None, str]:
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    out = work / "joined.mp4"
    ok, err = _ff([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
                   "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], REEL_TIMEOUT)
    return (out if ok else None), err


def _with_music(work: Path, clip: Path, total: float) -> tuple[Path | None, str]:
    out = work / "final.mp4"
    args = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip),
            "-f", "lavfi", "-t", f"{total:.3f}",
            "-i", "sine=frequency=110:sample_rate=44100",
            "-filter_complex",
            "[1:a]lowpass=f=500,tremolo=f=0.15:d=0.5,volume=0.10[pad];"
            "[0:a][pad]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(out)]
    ok, err = _ff(args, REEL_TIMEOUT)
    return (out if ok else None), err


def build_reel(urls: list[str], ep: int, options: object = None,
               lines: list[str] | None = None, title: str = "") -> tuple[dict, int]:
    """按顺序拼接本集成片。

    规格统一（704×1248 / 24fps / 44.1k 立体声）后可选：转场交叉淡化、逐镜字幕、
    片头片尾卡、本地合成底音。全在本地 ffmpeg 完成，不调上游、不花额度。
    """
    if not isinstance(urls, list) or not urls:
        return {"ok": False, "error": "没有可拼接的镜头"}, 400
    if len(urls) > REEL_MAX_SEG:
        return {"ok": False, "error": f"一次最多拼 {REEL_MAX_SEG} 段"}, 400
    opt, opt_err = normalize_opts(options)
    if opt_err:
        return {"ok": False, "error": opt_err}, 400
    paths: list[Path] = []
    for i, u in enumerate(urls):
        if not isinstance(u, str) or not VIDEO_PATH_RE.match(u):
            return {"ok": False, "error": f"第 {i + 1} 段路径不合法"}, 400
        path = ROOT / u.lstrip("/")
        if not path.exists() or path.stat().st_size < 1024:
            return {"ok": False, "error": f"第 {i + 1} 段文件不存在（可能只出了图）"}, 400
        paths.append(path)
    infos = []
    for i, path in enumerate(paths):
        info = probe_media(path)
        if not info or info["dur"] <= 0:
            return {"ok": False, "error": f"第 {i + 1} 段读不出时长"}, 400
        infos.append(info)
    line_list = lines if isinstance(lines, list) and len(lines) == len(paths) else []
    if not _reel_lock.acquire(timeout=1):
        return {"ok": False, "error": "正在拼接上一条，请稍后"}, 429
    work = GENERATED / f".reel-{int(time.time() * 1000)}"
    try:
        started = time.monotonic()
        work.mkdir(parents=True, exist_ok=True)
        clips: list[Path] = []
        durs: list[float] = []
        if opt["titles"]:
            head, err = _card(work, "head.mp4", 1.8, title or "未命名短剧", f"第 {max(1, int(ep))} 集")
            if not head:
                return {"ok": False, "error": "片头生成失败: " + err}, 500
            clips.append(head)
            durs.append(1.8)
        silent_segments = 0
        for i, (path, info) in enumerate(zip(paths, infos)):
            if not info["has_audio"]:
                silent_segments += 1
            seg, err = _segment(work, i, path, info,
                                [line_list[i]] if line_list else [], opt)
            if not seg:
                return {"ok": False, "error": f"第 {i + 1} 段处理失败: {err}"}, 500
            norm = probe_media(seg)
            if not norm or norm["dur"] <= 0:
                return {"ok": False, "error": f"第 {i + 1} 段规范化后读不出时长"}, 500
            clips.append(seg)
            durs.append(norm["dur"])
        if opt["titles"]:
            tail, err = _card(work, "tail.mp4", 1.8, "本片由 AI 生成",
                              "分镜图 flux-3-image · 视频 minimaxH3")
            if not tail:
                return {"ok": False, "error": "片尾生成失败: " + err}, 500
            clips.append(tail)
            durs.append(1.8)

        if opt["transition"] > 0 and len(clips) > 1:
            joined, err = _xfade_chain(work, clips, durs, opt["transition"])
        else:
            joined, err = _concat(work, clips)
        if not joined:
            return {"ok": False, "error": "拼接失败: " + err}, 500
        total = sum(durs) - opt["transition"] * max(0, len(clips) - 1)
        final = joined
        if opt["music"] == "drone":
            mixed, err = _with_music(work, joined, total)
            if not mixed:
                return {"ok": False, "error": "底音混音失败: " + err}, 500
            final = mixed

        GENERATED.mkdir(parents=True, exist_ok=True)
        name = f"reel-ep{max(1, int(ep))}-{int(time.time() * 1000)}.mp4"
        out = GENERATED / name
        out.write_bytes(final.read_bytes())
        info = probe_media(out) or {}
        append_video_log({"ts": datetime.now(timezone.utc).isoformat(), "kind": "reel",
                          "ep": ep, "segments": len(paths), "file": name,
                          "options": opt, "seconds": info.get("dur"),
                          "bytes": out.stat().st_size,
                          "elapsed_ms": int((time.monotonic() - started) * 1000),
                          "silent_segments": silent_segments})
        return {"ok": True, "url": f"/assets/generated/{name}", "segments": len(paths),
                "seconds": round(info.get("dur") or 0, 2),
                "bytes": out.stat().st_size,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "silentSegments": silent_segments,
                "options": opt}, 200
    finally:
        _reel_lock.release()
        shutil.rmtree(work, ignore_errors=True)


def reel_cloud_key() -> str:
    if REEL_CLOUD_ENV_KEY:
        return REEL_CLOUD_ENV_KEY
    try:
        return REEL_CLOUD_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def reel_cloud_ready() -> bool:
    return bool(REEL_CLOUD_BASE) and bool(reel_cloud_key())


def _cloud_request(method: str, path: str, body: bytes | None = None,
                   headers: dict | None = None, timeout: int = 120) -> tuple[dict, int]:
    hdrs = {"X-Reel-Key": reel_cloud_key()}
    hdrs.update(headers or {})
    req = urllib.request.Request(REEL_CLOUD_BASE + path, data=body, method=method, headers=hdrs)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}, resp.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            data = json.loads(detail)
        except json.JSONDecodeError:
            data = {"error": detail[:200]}
        return data, exc.code


def build_reel_cloud(urls: list[str], ep: int, options: object = None,
                     lines: list[str] | None = None, title: str = "") -> tuple[dict, int]:
    """把本集成片交给云端：逐段直传 → 提交任务 → 轮询 → 拉回本地。"""
    if not reel_cloud_ready():
        return {"ok": False, "error": "云端拼接未配置（qa/reel-cloud-key.txt 或 REEL_CLOUD_KEY）"}, 503
    if not isinstance(urls, list) or not urls:
        return {"ok": False, "error": "没有可拼接的镜头"}, 400
    if len(urls) > REEL_MAX_SEG:
        return {"ok": False, "error": f"一次最多拼 {REEL_MAX_SEG} 段"}, 400
    opt, opt_err = normalize_opts(options)
    if opt_err:
        return {"ok": False, "error": opt_err}, 400
    segments: list[dict] = []
    started = time.monotonic()
    for i, u in enumerate(urls):
        if not isinstance(u, str) or not VIDEO_PATH_RE.match(u):
            return {"ok": False, "error": f"第 {i + 1} 段路径不合法"}, 400
        path = ROOT / u.lstrip("/")
        if not path.exists() or path.stat().st_size < 1024:
            return {"ok": False, "error": f"第 {i + 1} 段文件不存在"}, 400
        name = hashlib.sha256(f"{path.stat().st_size}{path.name}{time.time()}".encode()).hexdigest()[:16]
        upload = f"{name}.mp4"
        data, status = _cloud_request("PUT", f"/v1/blob/{upload}", path.read_bytes(),
                                      {"Content-Type": "video/mp4"}, timeout=180)
        if status not in (200, 201) or not data.get("ok"):
            return {"ok": False, "error": f"第 {i + 1} 段上传失败：{data.get('error') or status}"}, 502
        segments.append({"upload": upload})
    payload = {"segments": segments, "options": opt, "title": title or "未命名短剧",
               "lines": [str(x)[:120] for x in (lines or [])][:REEL_MAX_SEG]}
    data, status = _cloud_request("POST", "/v1/reel", json.dumps(payload).encode(),
                                  {"Content-Type": "application/json"}, timeout=60)
    if status != 202 or not data.get("id"):
        return {"ok": False, "error": f"云端受理失败：{data.get('error') or status}"}, 502
    job = data["id"]
    deadline = started + REEL_CLOUD_TIMEOUT
    last = {}
    while time.monotonic() < deadline:
        time.sleep(1.0)
        last, st = _cloud_request("GET", f"/v1/reel/{job}", timeout=30)
        if st != 200:
            continue
        if last.get("status") == "succeeded":
            break
        if last.get("status") == "failed":
            return {"ok": False, "error": f"云端拼接失败：{last.get('error')}"}, 502
    else:
        return {"ok": False, "error": f"云端 {REEL_CLOUD_TIMEOUT}s 内没拼完（任务 {job}）"}, 504
    url = last.get("url") or ""
    if not url.startswith(REEL_CLOUD_BASE):
        return {"ok": False, "error": "云端返回了非预期地址"}, 502
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(url), timeout=180) as resp:
            blob = resp.read()
    except Exception as exc:
        return {"ok": False, "error": f"成片拉回失败：{type(exc).__name__}"}, 502
    GENERATED.mkdir(parents=True, exist_ok=True)
    name = f"reel-ep{max(1, int(ep))}-{int(time.time() * 1000)}.mp4"
    out = GENERATED / name
    out.write_bytes(blob)
    info = probe_media(out) or {}
    append_video_log({"ts": datetime.now(timezone.utc).isoformat(), "kind": "reel-cloud",
                      "ep": ep, "segments": len(segments), "file": name,
                      "options": opt, "seconds": info.get("dur"),
                      "bytes": out.stat().st_size, "job": job,
                      "elapsed_ms": int((time.monotonic() - started) * 1000)})
    return {"ok": True, "url": f"/assets/generated/{name}", "segments": len(segments),
            "seconds": round(info.get("dur") or 0, 2), "bytes": out.stat().st_size,
            "elapsed_ms": int((time.monotonic() - started) * 1000), "via": "cloud",
            "cloud": {"base": REEL_CLOUD_BASE, "job": job,
                      "seconds": last.get("seconds"), "elapsed_ms": last.get("elapsed_ms")}}, 200


def video_info() -> dict:
    return {
        "configured": avm_ready(),
        "base": AVM_BASE, "model": AVM_MODEL,
        "resolution": AVM_RESOLUTION, "duration": AVM_DURATION,
        "freeTier": f"turbo · {VIDEO_TIERS['free'][0]} · {VIDEO_TIERS['free'][1]}s",
        "tier": "free", "allowPaid": AVM_ALLOW_PAID,
        "tiers": {k: {"resolution": v[0], "duration": v[1]} for k, v in VIDEO_TIERS.items()},
        "calls": _video_calls, "maxCalls": MAX_VIDEOS,
        "frameAR": f"{VIDEO_AR[0]}:{VIDEO_AR[1]}",
        "ratios": sorted(r for r in VIDEO_RATIOS if r != "adaptive"),
        "minter": {"configured": bool(AVM_MINT and AVM_MINTER_URL),
                   "url": AVM_MINTER_URL, "target": AVM_MINTER_TARGET},
    }

def llm_key() -> str:
    """页面配置 > env > 本机 key 文件。文件按 0600 存，不回传、不写日志。"""
    if LLM_RT_KEY:
        return LLM_RT_KEY
    if LLM_ENV_KEY:
        return LLM_ENV_KEY
    try:
        return LLM_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def llm_ready() -> bool:
    return LLM_MODE != "off" and bool(LLM_BASE) and bool(LLM_MODEL) and bool(llm_key())


def llm_info() -> dict:
    return {"configured": llm_ready(), "mode": LLM_MODE, "model": LLM_MODEL,
            "base": LLM_BASE, "timeout": LLM_TIMEOUT,
            "keyConfigured": bool(llm_key()), "keyMasked": mask_key(llm_key()),
            "baseSource": LLM_SOURCE["base"], "keySource": LLM_SOURCE["key"],
            "modelSource": LLM_SOURCE["model"],
            "pageBase": LLM_RT.get("base") or "", "pageHasKey": bool(LLM_RT_KEY),
            "updatedAt": LLM_RT.get("updatedAt")}


class LLMError(RuntimeError):
    """模型层的可读错误：网络、HTTP、返回结构、内容校验都归到这里。"""


def llm_chat(messages: list, max_tokens: int = 1600, temperature: float = 0.8) -> tuple:
    """POST {base}/chat/completions。返回 (正文, 元信息)；失败抛 LLMError。"""
    if not llm_ready():
        raise LLMError("文本模型未配置（qa/llm-key.txt 或 LLM_KEY）")
    payload = {"model": LLM_MODEL, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": False}
    req = urllib.request.Request(
        f"{LLM_BASE}/chat/completions", data=json.dumps(payload).encode("utf-8"),
        method="POST", headers={"Authorization": f"Bearer {llm_key()}",
                                "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    try:
        with opener.open(req, timeout=LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:280]
        raise LLMError(f"上游 HTTP {exc.code}：{detail}") from None
    except json.JSONDecodeError:
        raise LLMError("上游返回的不是 JSON") from None
    except Exception as exc:                     # 超时、连接、DNS 都归这里
        raise LLMError(f"上游不可达：{type(exc).__name__} {exc}") from None
    try:
        choice = data["choices"][0]
        msg = choice.get("message") or {}
    except (KeyError, IndexError, TypeError):
        raise LLMError("上游没有 choices[0].message") from None
    text = msg.get("content")
    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    meta = {"model": data.get("model") or LLM_MODEL,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
            "finish_reason": choice.get("finish_reason"),
            "points": usage.get("points_consumed")}
    if not isinstance(text, str) or not text.strip():
        # 这类模型先花 token 思考；max_tokens 被思考吃光时只回空 content
        raise LLMError(f"上游返回空内容（finish_reason={choice.get('finish_reason')}"
                       f"，reasoning_tokens={details.get('reasoning_tokens')}）")
    return text, meta


def llm_json(messages: list, budget: int, temperature: float = 0.8) -> tuple:
    """要 JSON 的调用：空内容放大预算重试一次，非法 JSON 纠偏一次。"""
    convo = list(messages)
    last_err = "模型输出里找不到合法 JSON"
    for i, cap in enumerate((int(budget), min(9000, int(budget * 1.8)))):
        try:
            text, meta = llm_chat(convo, max_tokens=cap, temperature=temperature)
        except LLMError as exc:
            last_err = str(exc)
            if "空内容" in str(exc) and i == 0:
                continue                       # 预算被思考吃掉：放大再来一次
            raise
        obj = parse_json_object(text)
        if obj is not None:
            return obj, meta
        last_err = "模型输出里找不到合法 JSON"
        convo = convo + [{"role": "assistant", "content": text[:1500]},
                         {"role": "user", "content": "上面的输出不是合法 JSON。请只输出那一个 JSON 对象。"}]
        temperature = 0.3
    raise LLMError(last_err)


def parse_json_object(text: object) -> dict | None:
    """从模型输出里抠出第一个完整 JSON 对象。

    模型经常把 JSON 包在 ```json 围栏里，或在前面写一句"好的，这是剧本："。
    这里按花括号配对扫描，不用贪婪正则。
    """
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s.startswith("`"):
        s = re.sub(r"^`{3,}[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*`{3,}$", "", s)
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            ch = s[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(s[start:i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict):
                        return obj
                    break
        start = s.find("{", start + 1)
    return None


def _pick(obj: object, *names: str):
    """按别名取第一个有内容的字段 —— 中文模型经常换键名。"""
    if not isinstance(obj, dict):
        return None
    for n in names:
        if n in obj and obj[n] not in (None, "", [], {}):
            return obj[n]
    return None


def _unwrap(obj: object) -> object:
    """{"script": {...}} 这类单键包一层的情况，往下钻两层。"""
    cur = obj
    for _ in range(2):
        if isinstance(cur, dict) and len(cur) == 1:
            only = next(iter(cur.values()))
            if isinstance(only, dict):
                cur = only
                continue
        break
    return cur


def _clean(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit]


AGENT_MAX_MSGS = 12
AGENT_MAX_CHARS = 2000
AGENT_RATIOS = {"1:1", "9:16", "16:9", "3:4", "4:3"}
AGENT_SYSTEM = (
    "你是「即梦 AI」的创作 Agent，负责把用户的想法拆成可直接执行的生成方案。"
    "你只能输出一个 JSON 对象，不要 Markdown、不要解释文字。格式："
    '{"reply": "给用户看的中文回复，≤200 字",'
    ' "plan": [{"kind": "image"|"video", "title": "方案名 ≤12 字",'
    ' "prompt": "完整生成提示词，含主体/场景/光线/风格，≤300 字",'
    ' "ratio": "1:1"|"9:16"|"16:9"|"3:4"|"4:3", "count": 1}]}。'
    "plan 最多 3 条；视频固定 8 秒、比例为 9:16 或 16:9；"
    "提示词里不要要求模型写字，不要出现水印。"
)


def agent_mock() -> bool:
    return AGENT_MODE == "mock"


def agent_info() -> dict:
    return {"configured": llm_ready() or agent_mock(), "mode": AGENT_MODE,
            "mock": agent_mock(), "model": LLM_MODEL if not agent_mock() else "mock-agent",
            "maxMessages": AGENT_MAX_MSGS, "maxChars": AGENT_MAX_CHARS}


def normalize_agent(raw: object, hint: str = "") -> dict | None:
    """把模型输出收敛成 {reply, plan[]}；结构不成立就返回 None。"""
    if not isinstance(raw, dict):
        return None
    reply = _clean(_pick(raw, "reply", "message", "content", "回复", "说明", "text"), 1200)
    plans = _pick(raw, "plan", "plans", "actions", "steps", "方案", "计划")
    if isinstance(plans, dict):
        plans = [plans]
    if not isinstance(plans, list):
        plans = []
    out = []
    for item in plans[:3]:
        if isinstance(item, str):
            item = {"kind": "image", "prompt": item}
        if not isinstance(item, dict):
            continue
        raw_kind = str(_pick(item, "kind", "type", "类型") or "image").strip().lower()
        kind = {"image": "image", "img": "image", "pic": "image", "图片": "image", "图": "image",
                "video": "video", "短片": "video", "视频": "video", "动画": "video"}.get(raw_kind, "image")
        prompt = _clean(_pick(item, "prompt", "提示词", "description", "desc", "text"), 600)
        if not prompt:
            continue
        ratio = str(_pick(item, "ratio", "比例", "aspect") or "").strip()
        if ratio not in AGENT_RATIOS:
            ratio = "9:16" if kind == "video" else "1:1"
        try:
            count = int(_pick(item, "count", "数量", "n") or 1)
        except (TypeError, ValueError):
            count = 1
        count = 1 if kind == "video" else max(1, min(4, count))
        title = _clean(_pick(item, "title", "名称", "name"), 40) or ("视频方案" if kind == "video" else "图片方案")
        out.append({"kind": kind, "title": title, "prompt": prompt,
                    "ratio": ratio, "count": count})
    if not reply and not out:
        return None
    if not reply:
        reply = "我按你的描述拆了 %d 个方案，确认参数后可以逐条执行。" % len(out)
    return {"reply": reply, "plan": out}


def _agent_mock_reply(messages: list) -> dict:
    last = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last = m.get("content") or ""
            break
    seed = _clean(last, 120) or "一个雨夜的都市故事"
    return {
        "reply": "（MOCK 规划，零出站）我按「%s」拆成 2 个图片方案 + 1 个视频方案。"
                 "确认后点「填入创作页」，再自己按发送 —— Agent 不会替你花钱。" % seed[:40],
        "plan": [
            {"kind": "image", "title": "主视觉", "ratio": "9:16", "count": 1,
             "prompt": seed + "，电影感主视觉，侧逆光，浅景深，写实人物，画面干净，不出现任何文字"},
            {"kind": "image", "title": "环境空镜", "ratio": "16:9", "count": 1,
             "prompt": seed + " 的环境空镜，无人物，雨夜霓虹反光，电影级调色，画面干净，不出现任何文字"},
            {"kind": "video", "title": "8 秒运镜", "ratio": "9:16", "count": 1,
             "prompt": seed + "，镜头缓慢推近，轻微手持感，雨丝可见，画面不出现任何文字"},
        ],
    }


def run_agent(messages: object, context: object = None) -> tuple:
    if AGENT_MODE == "off":
        return {"ok": False, "error": "Agent 已在服务端关闭（JIMENG_AGENT=off）"}, 503
    if not isinstance(messages, list) or not messages:
        return {"ok": False, "error": "messages 必须是非空数组"}, 400
    if len(messages) > AGENT_MAX_MSGS:
        return {"ok": False, "error": f"对话最多 {AGENT_MAX_MSGS} 条"}, 400
    clean = []
    for m in messages:
        if not isinstance(m, dict):
            return {"ok": False, "error": "messages 里每一项都必须是对象"}, 400
        role = m.get("role")
        if role not in ("user", "assistant"):
            return {"ok": False, "error": "role 只支持 user|assistant"}, 400
        text = m.get("content")
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "error": "content 不能为空"}, 400
        if len(text) > AGENT_MAX_CHARS:
            return {"ok": False, "error": f"单条消息最长 {AGENT_MAX_CHARS} 字"}, 400
        clean.append({"role": role, "content": text.strip()})
    if clean[-1]["role"] != "user":
        return {"ok": False, "error": "最后一条必须是 user"}, 400
    if agent_mock():
        data = _agent_mock_reply(clean)
        data.update({"ok": True, "mock": True, "model": "mock-agent", "elapsed_ms": 0})
        return data, 200
    if not llm_ready():
        return {"ok": False, "error": "文本模型未配置：Agent 无法规划（qa/llm-key.txt 或 LLM_KEY）"}, 503
    convo = [{"role": "system", "content": AGENT_SYSTEM}]
    if isinstance(context, dict):
        bits = []
        if context.get("mode"):
            bits.append(f"当前模式：{_clean(context.get('mode'), 20)}")
        if context.get("prompt"):
            bits.append(f"创作页现有提示词：{_clean(context.get('prompt'), 300)}")
        if bits:
            convo.append({"role": "system", "content": "；".join(bits)})
    convo.extend(clean)
    try:
        raw, meta = llm_json(convo, 1400, 0.7)
    except LLMError as exc:
        return {"ok": False, "error": f"Agent 规划失败：{exc}"}, 502
    data = normalize_agent(raw)
    if data is None:
        return {"ok": False, "error": "模型返回的方案结构不合法（缺少 reply/plan）"}, 502
    data.update({"ok": True, "mock": False, "model": meta.get("model"), "meta": meta})
    return data, 200


def normalize_shots(raw: object, ep: int = 1, want: int = 6) -> tuple:
    """把模型给的镜头收敛成页面能用的形状：景别必须合法、至少 3 个、时长夹在 1–8s。"""
    raw = _unwrap(raw)
    if isinstance(raw, dict):
        raw = _pick(raw, "shots", "分镜", "镜头", "shot_list")
    if not isinstance(raw, list):
        return None, "模型没有返回 shots 数组"
    out = []
    for i, item in enumerate(raw[: want + 4]):
        if not isinstance(item, dict):
            continue
        desc = _clean(_pick(item, "desc", "画面", "描述", "画面描述", "content"), 120)
        if len(desc) < 4:
            continue
        size = _clean(_pick(item, "size", "景别", "shot_size"), 8)
        if size not in SHOT_SIZES:
            size = SHOT_SIZES[min(i, len(SHOT_SIZES) - 1)]
        try:
            dur = round(float(_pick(item, "dur", "时长", "duration", "seconds")), 1)
        except (TypeError, ValueError):
            dur = 3.0
        dur = max(1.0, min(8.0, dur))
        out.append({"n": len(out) + 1, "id": f"S{len(out) + 1:02d}", "ep": max(1, int(ep)),
                    "size": size, "dur": dur, "desc": desc,
                    "act": _clean(_pick(item, "act", "作用", "节拍"), 12),
                    "scene": _clean(_pick(item, "scene", "场景", "地点"), 40),
                    "line": _clean(_pick(item, "line", "台词", "音效", "sound"), 60) or "（环境声）"})
    if len(out) < 3:
        return None, f"模型只给了 {len(out)} 个可用镜头（至少 3 个）"
    return out[:want], ""


def normalize_script(raw: object, want_episodes: int) -> tuple:
    """校验模型返回的剧本：缺片名/梗概/人物就直接判失败，让上层退回模板。"""
    raw = _unwrap(raw)
    if not isinstance(raw, dict):
        return None, "模型返回的不是 JSON 对象"
    keys = "、".join(list(raw)[:8]) or "（空对象）"
    title = _clean(_pick(raw, "title", "片名", "剧名", "name"), 40)
    logline = _clean(_pick(raw, "logline", "一句话梗概", "梗概", "summary", "one_line", "简介"), 200)
    if len(title) < 2 or len(logline) < 6:
        return None, f"模型返回缺片名或一句话梗概（字段：{keys}）"
    chars = []
    cast_raw = _pick(raw, "characters", "人物", "角色", "roles", "cast") or []
    for c in cast_raw[:6] if isinstance(cast_raw, list) else []:
        if isinstance(c, str):                       # "苏念：26 岁，幸存者"
            parts = re.split(r"[：:—\-]", c, maxsplit=1)
            name, desc = _clean(parts[0], 24), _clean(parts[1] if len(parts) > 1 else "", 140)
        elif isinstance(c, dict):
            name = _clean(_pick(c, "name", "姓名", "角色", "名字"), 24)
            desc = _clean(_pick(c, "desc", "小传", "简介", "描述", "description"), 140)
        else:
            continue
        if name and desc:
            chars.append({"name": name, "desc": desc})
    if len(chars) < 2:
        return None, f"模型给的主要人物少于 2 个（字段：{keys}）"
    acts = []
    acts_raw = _pick(raw, "acts", "三幕", "三幕结构", "structure") or []
    for a in acts_raw[:5] if isinstance(acts_raw, list) else []:
        if isinstance(a, dict):
            name = _clean(_pick(a, "name", "名称", "幕"), 30)
            summary = _clean(_pick(a, "summary", "内容", "描述", "desc"), 260)
            if name and summary:
                acts.append({"name": name, "summary": summary})
    eps = []
    eps_raw = _pick(raw, "episodes", "分集", "分集大纲", "episode_list", "outline") or []
    if isinstance(eps_raw, dict):                    # {"1": "标题…"} 形态
        eps_raw = [{"title": v} for v in eps_raw.values()]
    for i, e in enumerate(eps_raw[: max(1, int(want_episodes))] if isinstance(eps_raw, list) else []):
        if isinstance(e, str):
            t, hook = _clean(e, 44), ""
        elif isinstance(e, dict):
            t = _clean(_pick(e, "title", "标题", "名称", "集名"), 44)
            hook = _clean(_pick(e, "hook", "钩子", "summary", "梗概"), 60)
        else:
            continue
        if t:
            eps.append({"n": i + 1, "title": t, "hook": hook or "结尾反转"})
    if not eps:
        return None, f"模型没有给出分集大纲（字段：{keys}）"
    shots, _ = normalize_shots(_pick(raw, "shots", "分镜", "镜头"), 1)
    return {"title": title, "logline": logline, "characters": chars,
            "acts": acts or [{"name": "第一幕", "summary": logline}],
            "episodes": eps, "shots": shots or []}, ""


LLM_SCRIPT_SYS = (
    "你是竖屏短剧编剧。只输出一个 JSON 对象，不要解释、不要 markdown 代码块。"
    "JSON 结构：{\"title\":str,\"logline\":str,"
    "\"characters\":[{\"name\":str,\"desc\":str}],"
    "\"acts\":[{\"name\":str,\"summary\":str}],"
    "\"episodes\":[{\"title\":str,\"hook\":str}],"
    "\"shots\":[{\"size\":str,\"scene\":str,\"desc\":str,\"line\":str}]}"
)

LLM_SHOTS_SYS = (
    "你是竖屏短剧的分镜师。只输出一个 JSON 对象，不要解释、不要 markdown 代码块。"
    "JSON 结构：{\"shots\":[{\"size\":str,\"scene\":str,\"desc\":str,\"line\":str,\"dur\":number}]}"
)


def llm_script(prompt: str, episodes: int) -> tuple:
    """一次调用写完：片名 / 梗概 / 人物 / 三幕 / 分集 / 第 1 集分镜。"""
    episodes = max(1, min(40, int(episodes or 10)))
    user = (
        f"创作需求：{prompt}\n"
        f"要求：{episodes} 集竖屏短剧。给出片名（不超过 12 字）、一句话梗概、3 个主要人物"
        f"（name + 一句人物小传）、三幕结构（name + 60 字内 summary）、"
        f"分集大纲（恰好 {episodes} 条，title + hook 钩子）、"
        "以及第 1 集的 6 个镜头（size 必须从 大全景/全景/中景/近景/特写/过肩/俯拍 里选；"
        "scene 写内景/外景 + 地点；desc 是不出现任何文字的纯画面描述，40 字内；"
        "line 是该镜台词或音效，25 字内；dur 是镜头秒数，1–8 之间）。"
    )
    # 预算要留够「思考 + 正文」：实测同题思考常占 800–2500 tokens
    budget = min(9000, 2600 + 260 * episodes)
    obj, meta = llm_json([{"role": "system", "content": LLM_SCRIPT_SYS},
                          {"role": "user", "content": user}], budget)
    script, err = normalize_script(obj, episodes)
    if script is None:
        raise LLMError(err)
    return script, meta


def llm_shots(prompt: str, ep: int, context: dict) -> tuple:
    """按整剧上下文重写某一集的 6 个镜头。"""
    ep = max(1, min(40, int(ep or 1)))
    ctx = context if isinstance(context, dict) else {}
    title = _clean(ctx.get("title"), 40) or "未命名短剧"
    logline = _clean(ctx.get("logline"), 200)
    cast = []
    for c in (ctx.get("characters") or [])[:4]:
        if isinstance(c, dict):
            cast.append(f"{_clean(c.get('name'), 20)}（{_clean(c.get('desc'), 60)}）")
    eps = [e for e in (ctx.get("episodes") or []) if isinstance(e, dict)]
    this = next((e for e in eps if int(e.get("n") or 0) == ep), {})
    prev = next((e for e in eps if int(e.get("n") or 0) == ep - 1), {})
    user = (
        f"剧名：{title}\n梗概：{logline}\n主要人物：{'；'.join(cast) or '未提供'}\n"
        f"本集：第 {ep} 集《{_clean(this.get('title'), 40) or '未命名'}》"
        f"（钩子：{_clean(this.get('hook'), 60) or '未提供'}）\n"
        f"上一集结尾：{_clean(prev.get('hook'), 60) or '这是第一集'}\n"
        f"创作需求：{prompt}\n"
        "请写这一集的 6 个镜头，构成完整起承转合：size 从 大全景/全景/中景/近景/特写/过肩/俯拍 里选，"
        "desc 是 40 字内、不出现任何文字的纯画面描述，line 是 25 字内台词或音效，dur 在 1–8 秒之间。"
    )
    obj, meta = llm_json([{"role": "system", "content": LLM_SHOTS_SYS},
                          {"role": "user", "content": user}], 2200)
    shots, err = normalize_shots(obj.get("shots"), ep)
    if shots is None:
        raise LLMError(err)
    return shots, meta


def append_llm_log(entry: dict) -> None:
    try:
        QA.mkdir(parents=True, exist_ok=True)
        with LLM_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _run_llm(kind: str, prompt: str, worker) -> tuple:
    """统一的门禁与记账：未配置 503、并发 429、上游/校验失败 502（都带 fallback 标记）。"""
    if len(prompt) > LLM_MAX_PROMPT:
        prompt = prompt[:LLM_MAX_PROMPT]
    if not llm_ready():
        return {"ok": False, "fallback": True,
                "error": "文本模型未配置（qa/llm-key.txt 或 LLM_KEY）"}, 503
    if not _llm_lock.acquire(timeout=1):
        return {"ok": False, "fallback": True, "error": "模型正在写上一稿，请稍后"}, 429
    started = time.monotonic()
    try:
        result, meta = worker()
    except LLMError as exc:
        append_llm_log({"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, "ok": False,
                        "model": LLM_MODEL, "prompt_chars": len(prompt),
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                        "error": str(exc)[:300]})
        return {"ok": False, "fallback": True, "error": str(exc)}, 502
    finally:
        _llm_lock.release()
    append_llm_log({"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, "ok": True,
                    "model": meta.get("model"), "prompt_chars": len(prompt),
                    "elapsed_ms": meta.get("elapsed_ms"),
                    "prompt_tokens": meta.get("prompt_tokens"),
                    "completion_tokens": meta.get("completion_tokens"),
                    "reasoning_tokens": meta.get("reasoning_tokens"),
                    "finish_reason": meta.get("finish_reason"),
                    "points": meta.get("points")})
    return {"ok": True, "fallback": False, "data": result, "meta": meta}, 200


def run_llm_script(prompt: str, episodes: int) -> tuple:
    return _run_llm("script", prompt, lambda: llm_script(prompt, episodes))


def run_llm_shots(prompt: str, ep: int, context: dict) -> tuple:
    return _run_llm("shots", prompt, lambda: llm_shots(prompt, ep, context))


_lock = threading.Lock()
_calls = 0
# 上游并发：本代理是多线程的（ThreadingHTTPServer），这里只做「有界并发」——
# 既不让 20 张图同时打满上游，也不再像以前那样一次只放一个请求过去。
IMG_CONCURRENCY = max(1, int(os.environ.get("JIMENG_CONCURRENCY", "4")))
IMG_QUEUE_TIMEOUT = float(os.environ.get("JIMENG_QUEUE_TIMEOUT", "240"))
_img_sem = threading.BoundedSemaphore(IMG_CONCURRENCY)
# ---------------- 任务队列 ----------------
# 「提交即返回」：HTTP 线程只负责入队，真正的上游往返交给固定数量的 worker。
TASK_WORKERS = max(1, int(os.environ.get("JIMENG_WORKERS", str(IMG_CONCURRENCY))))
TASK_QUEUE_MAX = max(1, int(os.environ.get("JIMENG_QUEUE_MAX", "64")))
TASK_RETRIES = max(0, int(os.environ.get("JIMENG_RETRIES", "2")))          # 首次之外的重试次数
TASK_BACKOFF_BASE = float(os.environ.get("JIMENG_BACKOFF_BASE", "0.8"))     # 秒
TASK_BACKOFF_CAP = float(os.environ.get("JIMENG_BACKOFF_CAP", "8"))         # 秒
TASK_JITTER = float(os.environ.get("JIMENG_JITTER", "1.0"))                 # 1.0 = full jitter
TASK_DISPATCH_JITTER = float(os.environ.get("JIMENG_DISPATCH_JITTER", "0.12"))
IDEM_TTL = float(os.environ.get("JIMENG_IDEM_TTL", "900"))
TASK_KEEP = max(8, int(os.environ.get("JIMENG_TASK_KEEP", "200")))
_inflight = 0
_inflight_peak = 0
_inflight_lock = threading.Lock()


def read_api_key(path: Path) -> str:
    """从 .env 读 BFL2OPENAI_API_KEY；不打印、不缓存到磁盘。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"^\s*BFL2OPENAI_API_KEY\s*=\s*(.*?)\s*$", text, re.M)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


def normalize_base(raw: object) -> str:
    """归一化上游基址：只接受 http(s)，去尾斜杠与 /v1，拒绝内嵌凭据/查询串/端口自环。"""
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    if not s or len(s) > 200 or not re.match(r"^https?://", s, re.I):
        return ""
    rest = s.split("://", 1)[1]
    host = rest.split("/", 1)[0]
    if "@" in host or not host:
        return ""
    if "?" in s or "#" in s:
        return ""
    s = s.rstrip("/")
    if s.lower().endswith("/v1"):
        s = s[:-3].rstrip("/")
    if not s.split("://", 1)[1]:
        return ""
    return s


def public_origin_ok(host: str, origin: str) -> bool:
    """公网部署的同源判定：只认配置过的站点，且 Origin 必须精确匹配。

    - 空 Origin（curl / 同源导航）放过；
    - 浏览器跨站请求会带攻击者站点的 Origin，这里一律拒绝；
    - 不做通配，避免把任意 1task.cn 子域都当成自己。
    """
    h = (host or "").strip().lower()
    if h not in PUBLIC_HOSTS:
        return False
    if not origin:
        return True
    return origin.rstrip("/").lower() in (f"https://{h}", f"http://{h}")


def origin_allowed(origin: str, port: int) -> bool:
    """同源判定：空 Origin（curl / 同源导航）放过，跨端口/跨协议/跨站一律拒绝。

    注意 port 必须是**实际监听端口**：多实例（8788/8790/8796…）各跑一个进程，
    写死默认端口会让其它实例的页面请求被判成跨站（曾直接表现为「出视频」全失败）。
    """
    if not origin:
        return True
    return bool(re.match(rf"^http://(127\.0\.0\.1|localhost|\[::1\]):{int(port)}$", origin, re.I))


def is_self_base(base: str) -> bool:
    """防止把上游指回自己，形成自调用死循环。"""
    m = re.match(r"^https?://(127\.0\.0\.1|localhost)(?::(\d+))?$", base, re.I)
    return bool(m) and (m.group(2) in (None, str(PORT)))


def mask_key(key: str) -> str:
    if not key:
        return ""
    return "••••••••" + key[-4:] if len(key) > 8 else "••••"


def load_runtime_config() -> dict:
    try:
        data = json.loads(RT_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "base": normalize_base(data.get("base") or ""),
        "apiKey": (data.get("apiKey") or "").strip() if isinstance(data.get("apiKey"), str) else "",
        # models 是快捷配置下发的图片模型白名单；读取时保留，resolve_config 再校验
        "models": data.get("models") if isinstance(data.get("models"), (list, str)) else [],
        "updatedAt": data.get("updatedAt"),
    }


def save_runtime_config(data: dict) -> None:
    """原子写 + 0600：密钥只落本机磁盘，不经浏览器。"""
    QA.mkdir(parents=True, exist_ok=True)
    tmp = RT_CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, RT_CONFIG_FILE)


def clear_runtime_config() -> None:
    try:
        RT_CONFIG_FILE.unlink()
    except FileNotFoundError:
        pass


IMAGE_ENDPOINT_SUFFIX = ("/v1/images/generations", "/images/generations", "/v1/images/edits")
CHAT_ENDPOINT_SUFFIX = ("/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions")


def strip_endpoint(raw: object, suffixes) -> str:
    """把「完整端点 URL」还原成 base。

    用户从文档里复制的是 `https://bfl.1task.cn/v1/images/generations` 这种整条地址，
    而代理自己会拼 `/v1/images/generations`；不剥掉就会拼成两遍。
    """
    if not isinstance(raw, str):
        return ""
    s = raw.strip().rstrip("/")
    low = s.lower()
    for suf in suffixes:
        if low.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)].rstrip("/")
    return s


def normalize_image_base(raw: object) -> str:
    """图像网关基址：先去端点后缀，再按 normalize_base 归一化（去 /v1）。"""
    return normalize_base(strip_endpoint(raw, IMAGE_ENDPOINT_SUFFIX))


def normalize_chat_base(raw: object) -> str:
    """chat 基址归一化：**保留 /v1**（OpenAI 兼容约定），只有主机时自动补 /v1。

    与 normalize_base 的差别就在这里：图像网关要的是 host（代码里再拼 /v1/images/
    generations），而 chat 端点通常是 {base}/chat/completions，base 自带 /v1。
    """
    if not isinstance(raw, str):
        return ""
    s = strip_endpoint(raw, CHAT_ENDPOINT_SUFFIX)
    if not s or len(s) > 240 or not re.match(r"^https?://", s, re.I):
        return ""
    rest = s.split("://", 1)[1]
    if not rest or "@" in rest.split("/", 1)[0]:
        return ""
    if "?" in s or "#" in s:
        return ""
    if "/" not in rest:
        s = f"{s}/v1"
    return s


def load_runtime_llm() -> dict:
    try:
        data = json.loads(RT_LLM_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "base": normalize_chat_base(data.get("base") or ""),
        "key": (data.get("key") or "").strip() if isinstance(data.get("key"), str) else "",
        "model": (data.get("model") or "").strip() if isinstance(data.get("model"), str) else "",
        "updatedAt": data.get("updatedAt"),
    }


def save_runtime_llm(data: dict) -> None:
    """原子写 + 0600，跟图像网关的 runtime-config.json 同一套纪律。"""
    QA.mkdir(parents=True, exist_ok=True)
    tmp = RT_LLM_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, RT_LLM_FILE)


def clear_runtime_llm() -> None:
    try:
        RT_LLM_FILE.unlink()
    except FileNotFoundError:
        pass


def refresh_llm() -> dict:
    """重算文本模型生效值（页面 > env > 文件），保存后调用即可热切换。"""
    global LLM_BASE, LLM_MODEL, LLM_RT, LLM_RT_KEY
    LLM_RT = load_runtime_llm()
    LLM_BASE = LLM_RT.get("base") or LLM_ENV_BASE
    LLM_MODEL = LLM_RT.get("model") or LLM_ENV_MODEL
    LLM_RT_KEY = LLM_RT.get("key") or ""
    if LLM_RT.get("base"):
        LLM_SOURCE["base"] = "page"
    else:
        LLM_SOURCE["base"] = "env"
    if LLM_RT_KEY:
        LLM_SOURCE["key"] = "page"
    elif LLM_ENV_KEY:
        LLM_SOURCE["key"] = "env"
    else:
        try:
            LLM_SOURCE["key"] = "file" if LLM_KEY_FILE.read_text(encoding="utf-8").strip() else "none"
        except OSError:
            LLM_SOURCE["key"] = "none"
    LLM_SOURCE["model"] = "page" if LLM_RT.get("model") else "env"
    return {"base": LLM_BASE, "model": LLM_MODEL, "keyConfigured": bool(llm_key()),
            "baseSource": LLM_SOURCE["base"], "keySource": LLM_SOURCE["key"]}


def pick_base(page_base: str, env_base: str, default: str) -> tuple[str, str]:
    """优先级：页面配置 > 部署环境变量 > 默认值。返回 (值, 来源)。"""
    pb = normalize_base(page_base)
    if pb:
        return pb, "page"
    eb = normalize_base(env_base)
    if eb:
        return eb, "env"
    return default, "default"


def pick_key(page_key: str, env_key: str, env_file_key: str, default_key: str) -> tuple[str, str]:
    """优先级：页面配置 > BFL2OPENAI_API_KEY > BFL_KEY_FILE > 默认 .env。"""
    if page_key:
        return page_key, "page"
    if env_key:
        return env_key, "env"
    if env_file_key:
        return env_file_key, "env-file"
    if default_key:
        return default_key, "default-file"
    return "", "none"


MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,59}$")


def parse_model_list(raw: object) -> tuple[list[str], str]:
    """解析 models=flux-3-image,flux-4-image 或数组；去重、限长、拒绝空白/换行。

    只允许模型 ID 常用字符；不接受空项以外的任意内容。返回 (列表, 错误)。
    """
    if raw in (None, "", []):
        return [], ""
    if isinstance(raw, str):
        parts = raw.split(",")
    elif isinstance(raw, list):
        parts = raw
    else:
        return [], "models 必须是逗号分隔的字符串或数组"
    out: list[str] = []
    for item in parts:
        if not isinstance(item, str):
            return [], "models 的每一项都必须是字符串"
        m = item.strip()
        if not m:
            continue
        if len(m) > 60 or not MODEL_ID_RE.match(m):
            return [], f"模型名不合法：{m[:40]}（只允许字母数字与 . _ : / @ + -）"
        if m not in out:
            out.append(m)
    if len(out) > MAX_MODELS:
        return [], f"models 最多 {MAX_MODELS} 个（当前 {len(out)} 个）"
    return out, ""


def resolve_config() -> dict:
    """按优先级算出当前生效配置，并标明每一项的来源。"""
    rt = load_runtime_config()
    env_file_key = read_api_key(Path(ENV_KEY_FILE)) if ENV_KEY_FILE else ""
    default_key = read_api_key(DEFAULT_KEY_FILE)
    base, base_src = pick_base(rt.get("base") or "", ENV_BASE, DEFAULT_BASE)
    key, key_src = pick_key(rt.get("apiKey") or "", ENV_KEY, env_file_key, default_key)
    page_models, _ = parse_model_list(rt.get("models"))
    env_models, env_models_err = parse_model_list(ENV_MODELS_RAW)
    models = page_models or env_models or [DEFAULT_MODEL]
    return {"base": base, "baseSource": base_src, "key": key, "keySource": key_src,
            "models": models, "model": models[0],
            "modelsSource": "page" if page_models else ("env" if env_models else "default"),
            "modelsError": env_models_err if (env_models_err and not page_models) else "",
            "env": {"base": normalize_base(ENV_BASE),
                    "hasKey": bool(ENV_KEY) or bool(env_file_key)},
            "page": {"base": rt.get("base") or "", "hasKey": bool(rt.get("apiKey")),
                     "updatedAt": rt.get("updatedAt")}}


# 模型 → {"usd": 单次美元价, "credits": 单张积分, "source": upstream|default|env, "reason": ...}
# 多模型下同一个积分不能被所有模型共用：flux $0.2 与 seedream $0.0164 差 12 倍。
PRICE_TABLE: dict[str, dict] = {}


def refresh_pricing(force: bool = False, timeout: float | None = None) -> dict:
    """把「每个模型一张 1K 图多少积分」对齐到上游定价表。

    优先级：JIMENG_IMAGE_CREDITS（显式钉死）> 上游 /api/pricing > 默认 0.038 USD。
    一次拉表算全部模型；解析失败的模型回退默认值并记 reason，绝不猜价。
    """
    global IMAGE_CREDITS, IMAGE_PRICE_USD, IMAGE_PRICE_SOURCE
    models = list(CONFIG.get("models") or [MODEL])
    if MODEL not in models:
        models.insert(0, MODEL)
    PRICE_TABLE.clear()
    if IMAGE_CREDITS_ENV:
        for m in models:
            PRICE_TABLE[m] = {"usd": float(IMAGE_CREDITS), "credits": float(IMAGE_CREDITS),
                              "source": "env", "reason": None}
        return {"usd": IMAGE_PRICE_USD, "credits": IMAGE_CREDITS,
                "source": "env", "model": MODEL, "reason": None}
    try:
        got = billing.upstream_prices(BASE, models, force=force, timeout=timeout)
    except Exception as exc:              # 定价查询不能把计费整条链路打挂
        got = {m: {"supported": False, "reason": f"定价查询失败：{type(exc).__name__}"}
               for m in models}
    for m, info in got.items():
        ok = bool(info.get("supported")) and info.get("credits") is not None
        PRICE_TABLE[m] = {"usd": info.get("usd") if ok else None,
                          "credits": float(info["credits"]) if ok else None,
                          "source": "upstream" if ok else "default",
                          "reason": info.get("reason")}
    cur = PRICE_TABLE.get(MODEL) or {}
    if cur.get("source") == "upstream" and cur.get("credits") is not None:
        IMAGE_PRICE_USD = float(cur["usd"])
        IMAGE_CREDITS = float(cur["credits"])
        IMAGE_PRICE_SOURCE = "upstream"
    else:
        IMAGE_PRICE_USD = DEFAULT_IMAGE_USD
        IMAGE_CREDITS = round(DEFAULT_IMAGE_USD * billing.CREDITS_PER_USD, 2)
        IMAGE_PRICE_SOURCE = "default"
    return {"usd": IMAGE_PRICE_USD, "credits": IMAGE_CREDITS,
            "source": IMAGE_PRICE_SOURCE, "model": MODEL,
            "reason": (got.get(MODEL) or {}).get("reason")}


def image_credits_for(model: str | None = None) -> float:
    """按模型取「一张 1K 图」的积分；该模型没有上游价就退回当前默认值。

    计费口径必须跟着请求里的模型走：否则选便宜的 seedream 也按 flux 的 20 积分扣。
    """
    m = model or MODEL
    hit = PRICE_TABLE.get(m) or {}
    if hit.get("source") == "upstream" and hit.get("credits") is not None:
        return float(hit["credits"])
    return float(IMAGE_CREDITS)


def refresh_config() -> dict:
    """重算全局生效值；保存配置后调用即可热切换，无需重启。"""
    global BASE, API_KEY, MOCK_MODE, UPSTREAM_MODE, REAL_READY, CONFIG, MODEL
    CONFIG = resolve_config()
    BASE = CONFIG["base"]
    API_KEY = CONFIG["key"]
    MODEL = CONFIG["model"]
    MOCK_MODE = bool(re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?", BASE))
    UPSTREAM_MODE = "mock" if MOCK_MODE else "live"
    REAL_READY = REAL and bool(API_KEY)
    refresh_llm()
    return CONFIG


def public_config() -> dict:
    """可下发浏览器的配置视图：绝不含明文密钥。"""
    c = CONFIG or resolve_config()
    return {
        "base": c["base"], "baseSource": c["baseSource"],
        "keyConfigured": bool(c["key"]), "keySource": c["keySource"],
        "keyMasked": mask_key(c["key"]),
        "pageBase": c["page"]["base"], "pageHasKey": c["page"]["hasKey"],
        "envBase": c["env"]["base"], "envHasKey": c["env"]["hasKey"],
        "model": c.get("model") or MODEL,
        "models": c.get("models") or [DEFAULT_MODEL],
        "modelsSource": c.get("modelsSource") or "default",
        "defaultBase": DEFAULT_BASE,
        "defaultModel": DEFAULT_MODEL,
        "updatedAt": c["page"].get("updatedAt"),
        "billing": billing_info(),
    }


# ---------------- URL 快捷配置（?base_url=…&api_key=…） ----------------
# 为什么在服务端受理：密钥永远不落地到页面 JS / 浏览器历史 —— 收到后立刻写盘
# （0600）、302 到去掉敏感参数的同一路径，前端只看到一条结果提示。
QUICK_BASE_KEYS = ("base_url", "base-url", "baseurl", "base", "api_base", "api-base", "endpoint")
QUICK_KEY_KEYS = ("api_key", "api-key", "apikey", "apiKey", "key", "token")
QUICK_MODEL_KEYS = ("models", "model_list", "model-list", "model_ids")
QUICK_CTRL_KEYS = ("target", "reset", "model", "llm", "quick", *QUICK_MODEL_KEYS)
QUICK_DROP = {k.lower() for k in QUICK_BASE_KEYS + QUICK_KEY_KEYS + QUICK_CTRL_KEYS}


def quick_get(params: dict, names) -> str:
    """按别名取参数值（大小写不敏感，只取第一个命中的非空值）。"""
    low = {str(k).lower(): v for k, v in (params or {}).items()}
    for n in names:
        v = low.get(n.lower())
        if isinstance(v, str):
            return v.strip()
    return ""


def is_quick_request(params: dict) -> bool:
    low = {str(k).lower() for k in (params or {})}
    return bool(low & {k.lower() for k in QUICK_BASE_KEYS + QUICK_KEY_KEYS + QUICK_MODEL_KEYS})


def quick_target(params: dict) -> str:
    """image（默认）| llm | all；不认识的取值返回 invalid，由调用方报错。"""
    q = {str(k).lower(): v for k, v in (params or {}).items()}
    raw = str(q.get("target") or "").strip().lower()
    if not raw:
        return "llm" if str(q.get("llm", "")).lower() in ("1", "true", "yes", "on") else "image"
    return {"image": "image", "img": "image", "llm": "llm", "text": "llm",
            "chat": "llm", "all": "all", "both": "all", "*": "all"}.get(raw, "invalid")


def quick_clean_query(params: dict) -> dict:
    """302 用的干净查询串：去掉所有敏感与一次性参数，其余（view/theme…）原样保留。"""
    return {k: v for k, v in (params or {}).items()
            if not (isinstance(k, str) and k.lower() in QUICK_DROP | {"_qc", "_qt", "_qb", "_qk", "_qm"})}


def apply_quick_config(params: dict) -> tuple[dict, int]:
    """把快捷配置写进运行配置。返回 (响应体, HTTP 状态)；响应体永不含明文密钥。"""
    target = quick_target(params)
    if target == "invalid":
        return {"ok": False, "error": "target 只支持 image | llm | all"}, 400
    reset = quick_get(params, ("reset",)).lower() in ("1", "true", "yes", "on")
    raw_base = quick_get(params, QUICK_BASE_KEYS)
    raw_key = quick_get(params, QUICK_KEY_KEYS)
    raw_model = quick_get(params, ("model",))
    raw_models = quick_get(params, QUICK_MODEL_KEYS)
    models, models_err = parse_model_list(raw_models)
    if models_err:
        return {"ok": False, "error": models_err}, 400

    if not reset and not raw_base and not raw_key and not raw_model and not raw_models:
        return {"ok": False, "error": "缺少 base_url / api_key（清空请用 reset=1）"}, 400
    if raw_key and (len(raw_key) > 200 or re.search(r"\s", raw_key)):
        return {"ok": False, "error": "api_key 不合法：不可含空白且不超过 200 字符"}, 400
    if raw_model and (len(raw_model) > 60 or re.search(r"[\r\n]", raw_model)):
        return {"ok": False, "error": "model 不合法：不超过 60 字符且不能换行"}, 400

    img_base = ""
    chat_base = ""
    if raw_base:
        if target in ("image", "all"):
            img_base = normalize_image_base(raw_base)
            if not img_base:
                return {"ok": False, "error": "base_url 不合法：需 http(s)://host[:port]，不带查询串"}, 400
            if is_self_base(img_base):
                return {"ok": False, "error": "base_url 不能指向本代理自身"}, 400
        if target in ("llm", "all"):
            chat_base = normalize_chat_base(raw_base)
            if not chat_base:
                return {"ok": False, "error": "base_url 不合法：需 http(s)://host[:port]，不带查询串"}, 400

    applied: dict = {"target": target, "reset": reset, "base": "", "keyMasked": "",
                     "model": "", "models": [], "imageModel": ""}

    if target in ("image", "all"):
        rt = load_runtime_config()
        if reset:
            rt = {}
        else:
            if img_base:
                rt["base"] = img_base
            if raw_key:
                rt["apiKey"] = raw_key
            if models:
                rt["models"] = models
        rt["updatedAt"] = datetime.now(timezone.utc).isoformat()
        if rt.get("base") or rt.get("apiKey") or rt.get("models"):
            save_runtime_config(rt)
        else:
            clear_runtime_config()
        refresh_config()
        applied["base"] = BASE
        applied["keyMasked"] = mask_key(API_KEY)
        applied["models"] = list(CONFIG.get("models") or [DEFAULT_MODEL])
        applied["imageModel"] = MODEL

    if target in ("llm", "all"):
        rl = load_runtime_llm()
        if reset:
            rl = {}
        else:
            if chat_base:
                rl["base"] = chat_base
            if raw_key:
                rl["key"] = raw_key
            if raw_model:
                rl["model"] = raw_model
        rl["updatedAt"] = datetime.now(timezone.utc).isoformat()
        if rl.get("base") or rl.get("key") or rl.get("model"):
            save_runtime_llm(rl)
        else:
            clear_runtime_llm()
        refresh_llm()
        if target == "llm":
            applied["base"] = LLM_BASE
            applied["keyMasked"] = mask_key(llm_key())
        if LLM_RT.get("model") or target == "llm":
            applied["model"] = LLM_MODEL

    append_quick_log({"ts": datetime.now(timezone.utc).isoformat(), "target": target,
                      "reset": reset, "base": applied["base"],
                      "keyTail": (applied["keyMasked"][-4:] if applied["keyMasked"] else ""),
                      "model": applied["model"], "models": applied["models"]})
    return {"ok": True, "applied": applied, "config": public_config(),
            "llm": llm_info(), "real": REAL_READY, "upstreamMode": UPSTREAM_MODE}, 200


BLOCKED_TEST_HOSTS = {"169.254.169.254", "metadata.google.internal"}


def probe_models(base: str, key: str, timeout: float = 8.0) -> dict:
    """只读探测 /v1/models：验证 base + key 是否可用。绝不发图片生成请求。"""
    started = time.monotonic()
    elapsed = lambda: int((time.monotonic() - started) * 1000)
    if not base:
        return {"ok": False, "error": "Base URL 为空", "elapsedMs": 0, "models": []}
    host = base.split("://", 1)[-1].split("/", 1)[0].split(":")[0].lower()
    if host in BLOCKED_TEST_HOSTS:
        return {"ok": False, "error": "该地址不允许探测", "elapsedMs": 0, "models": []}
    if not key:
        return {"ok": False, "error": "未配置 API Key", "elapsedMs": 0, "models": []}
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                 "User-Agent": "jimeng-clone-local-proxy/1.0"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw, status = resp.read(256 * 1024), resp.status
    except urllib.error.HTTPError as exc:
        return {"ok": False, "httpStatus": exc.code, "elapsedMs": elapsed(),
                "error": f"HTTP {exc.code}", "models": []}
    except Exception as exc:
        return {"ok": False, "elapsedMs": elapsed(), "error": str(exc)[:200], "models": []}
    ids: list[str] = []
    try:
        data = json.loads(raw)
        for it in (data.get("data") or []) if isinstance(data, dict) else []:
            if isinstance(it, dict) and it.get("id"):
                ids.append(str(it["id"]))
    except Exception:
        return {"ok": False, "httpStatus": status, "elapsedMs": elapsed(),
                "error": "返回不是合法的模型列表 JSON", "models": []}
    return {"ok": True, "httpStatus": status, "elapsedMs": elapsed(),
            "models": ids[:60], "count": len(ids), "hasModel": MODEL in ids, "model": MODEL}


refresh_config()


# ---------------- 纯函数（可单测，不碰网络） ----------------

def append_quick_log(entry: dict) -> None:
    """快捷配置审计：只记 base 与密钥末四位，绝不落明文。"""
    try:
        with (QA / "quick-config.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def normalize_prompt(prompt: object) -> str:
    """去首尾空白、折叠 3 行以上空行；超长截断到 MAX_PROMPT。"""
    if not isinstance(prompt, str):
        return ""
    s = re.sub(r"\n{3,}", "\n\n", prompt.strip())
    return s[:MAX_PROMPT]


def parse_refs(raw: object) -> tuple[list[str], str]:
    """校验并归一化参考图（data URI）。返回 (列表, 错误信息)。

    只接受 data:image/{png,jpeg,webp};base64,...：拒绝 http URL 是为了避免把
    任意外链当图床（SSRF 面），也让「发了什么」可被逐字节审计。
    """
    if raw in (None, [], ""):
        return [], ""
    if not isinstance(raw, list):
        return [], "refs 必须是数组"
    if len(raw) > MAX_REFS:
        return [], f"参考图最多 {MAX_REFS} 张（当前 {len(raw)} 张）"
    out, total = [], 0
    for i, item in enumerate(raw, 1):
        uri = item if isinstance(item, str) else (item or {}).get("url") if isinstance(item, dict) else None
        if not isinstance(uri, str) or not uri:
            return [], f"第 {i} 张参考图缺少内容"
        m = DATA_URI_RE.match(uri.strip())
        if not m:
            return [], f"第 {i} 张参考图必须是 data:image/...;base64 形式"
        mime = m.group(1).lower()
        if mime == "image/jpg":
            mime = "image/jpeg"
        if mime not in REF_MIMES:
            return [], f"第 {i} 张参考图格式不支持（仅 png/jpeg/webp）"
        b64 = re.sub(r"\s+", "", m.group(2))
        try:
            blob = base64.b64decode(b64, validate=True)
        except Exception:
            return [], f"第 {i} 张参考图 base64 解码失败"
        if not blob:
            return [], f"第 {i} 张参考图内容为空"
        if len(blob) > MAX_REF_BYTES:
            return [], f"第 {i} 张参考图超过 {MAX_REF_BYTES // (1024 * 1024)}MB"
        total += len(blob)
        if total > MAX_REFS_TOTAL:
            return [], f"参考图总大小超过 {MAX_REFS_TOTAL // (1024 * 1024)}MB"
        # 统一成规范写法（jpeg 别名、去空白），下游拿到的就是审计过的那份
        out.append(f"data:{mime};base64,{b64}")
    return out, ""


def pick_request_model(raw: object) -> tuple[str, str]:
    """把客户端选的模型收敛到当前 models 白名单；空值用默认模型。"""
    if raw in (None, ""):
        return MODEL, ""
    if not isinstance(raw, str):
        return "", "model 必须是字符串"
    m = raw.strip()
    if not m:
        return MODEL, ""
    allowed = CONFIG.get("models") or [MODEL]
    if m not in allowed:
        return "", f"model 不在本次允许列表：{m[:60]}"
    return m, ""


def build_upstream_payload(prompt: str, images: list[str] | None = None,
                           model: str | None = None) -> dict:
    """构造发往 bfl2openai 的 OpenAI 兼容请求体；尺寸/张数由服务端钉死。

    有参考图时带 `image` 数组：第 1 张是画布，其余作为参考图，走图生图/合成。
    """
    payload = {
        "model": model or MODEL,
        "prompt": prompt,
        "size": FIXED_SIZE,
        "resolution": FIXED_RESOLUTION,
        "n": 1,
        "response_format": "b64_json",
    }
    if images:
        payload["image"] = list(images)
    return payload


def image_url_of(payload: object) -> str:
    """上游把图给成 http(s) 地址时取出第一条；否则返回空串。"""
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return ""
    # new-api 不同渠道回的位置不一样：url / image_url，甚至把链接塞进 b64_json；
    # 还有回相对路径（/p/xxx）的，按上游 base 拼回绝对地址。
    # 🔴 不能只看「以 / 开头」：JPEG 的 base64 就是 /9j/ 开头，会把图片数据当成地址。
    for k in ("url", "image_url", "b64_json", "base64", "data"):
        v = data[0].get(k)
        if not isinstance(v, str):
            continue
        if v.startswith(("http://", "https://")):
            return v
        if v.startswith("/") and not _looks_base64(v):
            return v
    return ""


def _looks_base64(s: str) -> bool:
    """区分「相对地址」与「以 / 开头的 base64」：长度够且全是 base64 字符就按数据看。"""
    if len(s) < 256:
        return False
    return re.fullmatch(r"[A-Za-z0-9+/=\s]+", s[:4096]) is not None


def _url_under_base(url: str, base: str) -> bool:
    """下载地址是否属于上游自身（只有同源才带上 Authorization）。"""
    try:
        return url.startswith((base or "").rstrip("/") + "/") or url == (base or "").rstrip("/")
    except Exception:
        return False


def download_image(url: str, timeout: float | None = None,
                   base: str | None = None) -> bytes:
    """把上游给的图片地址下载成字节：只认 http(s)，限 32MB，校验 magic。

    只有地址落在上游 base 下才带 Authorization —— 别把 key 送给第三方对象存储。
    """
    headers = {"User-Agent": "jimeng-clone-local-proxy/1.0", "Accept": "image/*"}
    if base and _url_under_base(url, base):
        headers["Authorization"] = f"Bearer {API_KEY}"
    req = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout or 60) as resp:
        raw = resp.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("image too large (>32MB)")
    if len(raw) < 1024:
        raise ValueError(f"image too small ({len(raw)} bytes)")
    if not (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")):
        raise ValueError("downloaded file is neither PNG nor JPEG")
    return raw


def extract_image(payload: object) -> bytes:
    """从上游响应提取第一张图片的二进制；支持 b64_json / data URI / url 字段。"""
    if not isinstance(payload, dict):
        raise ValueError("upstream response is not an object")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("upstream returned empty data[]")
    item = data[0]
    if not isinstance(item, dict):
        raise ValueError("upstream data[0] is not an object")
    b64 = item.get("b64_json") or item.get("base64") or item.get("url") or item.get("data")
    if not isinstance(b64, str) or not b64:
        raise ValueError("upstream data[0] has no image payload")
    if b64.startswith("data:"):
        if "," not in b64:
            raise ValueError("malformed data URI")
        b64 = b64.split(",", 1)[1]
    if b64.startswith(("http://", "https://")):
        raise ValueError("upstream returned a URL; this proxy expects inline base64")
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise ValueError(f"invalid base64: {exc}") from exc
    if len(raw) < 1024:
        raise ValueError(f"image too small ({len(raw)} bytes)")
    if not (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")):
        raise ValueError("image is neither PNG nor JPEG")
    return raw


def estimate_credits(mode: str, count: int, resolution: str, duration: int, real: bool) -> int:
    """页面积分估算的镜像实现（与 app.js 同规则，单测锁死口径）。"""
    if real and mode == "image":
        return round(float(IMAGE_CREDITS), 2)  # 1 张 1K；单价由上游定价表决定
    if real and mode == "video":
        return 0   # 免费档（turbo · 720p · 8s）上游 paid=false，页面积分不动
    base = {"image": 100, "video": 18, "agent": 60}.get(mode, 100)
    multiplier = {"1K": 1, "2K": 2, "1080P": 1, "720P": 1}.get(resolution, 1)
    if mode == "video":
        return base * count * duration * multiplier
    return base * count * multiplier


# ---------------- 积分：上游额度 + 本地预扣/结算 ----------------

def billing_info() -> dict:
    """廉价视图：不访问网络，供 /api/status 与配置弹窗使用。"""
    return {"enabled": BILLING_ENABLED, "mode": BILLING_MODE,
            "imageCredits": IMAGE_CREDITS,
            "priceUsd": IMAGE_PRICE_USD, "priceSource": IMAGE_PRICE_SOURCE,
            "prices": {k: v.get("credits") for k, v in PRICE_TABLE.items()},
            "model": MODEL,
            "creditsPerUsd": billing.CREDITS_PER_USD,
            "quotaPerUsd": billing.QUOTA_PER_USD,
            "db": BILLING.path.name}


def billing_snapshot(force: bool = False, limit: int = 20) -> dict:
    """当前积分视图：优先用 new-api 令牌额度，失败/不支持时用本地账本。

    available = 资金来源余额 - 本地冻结；冻结是「已提交但还没结算」的预扣，
    防止并发提交把同一笔余额花两次。
    """
    price = refresh_pricing(force=force)
    ledger = BILLING.stats()
    up = None
    if BILLING_ENABLED and API_KEY and BASE:
        up = billing.upstream_balance(BASE, API_KEY, force=force)
    supported = bool(up and up.get("supported"))
    unlimited = bool(supported and up.get("unlimited"))
    # 只有两类会真正拦截：① 上游确实给了 new-api 额度；② 运维显式选了本地账本。
    # auto + 上游无 billing 端点 = shadow（只展示，不拦截），避免拿假余额挡住生成。
    enforce = bool(BILLING_ENABLED and (supported or BILLING_MODE == "local"))
    # 显式 local = 资金来源用本地账本；auto/upstream 下上游可读就用上游。
    if supported and BILLING_MODE != "local":
        billing_mode = "upstream"
        source = "new-api"
        base = 1e12 if unlimited else max(0.0, float(up.get("remainingCredits") or 0))
        total = up.get("totalCredits")
        used = up.get("usedCredits")
        remaining = up.get("remainingCredits")
    else:
        billing_mode = "local" if BILLING_MODE == "local" else "shadow"
        source = "local-ledger"
        base = float(ledger["balance"])
        total = None
        used = None
        remaining = float(ledger["balance"])
    available = base if unlimited else max(0.0, round(base - ledger["held"], 2))
    return {
        "ok": True, "enabled": BILLING_ENABLED, "mode": BILLING_MODE,
        "billingMode": billing_mode, "source": source, "supported": supported,
        "enforce": enforce, "unlimited": unlimited,
        "total": total, "used": used, "remaining": remaining,
        "balance": ledger["balance"], "held": ledger["held"],
        "available": available, "base": base,
        "todaySpent": ledger["todaySpent"],
        "imageCredits": IMAGE_CREDITS,
        "priceUsd": IMAGE_PRICE_USD, "priceSource": IMAGE_PRICE_SOURCE,
        "priceModel": price.get("model"), "priceReason": price.get("reason"),
        "prices": {k: v.get("credits") for k, v in PRICE_TABLE.items()},
        "priceDetails": PRICE_TABLE,
        "creditsPerUsd": billing.CREDITS_PER_USD,
        "quotaPerUsd": billing.QUOTA_PER_USD,
        "upstream": None if up is None else {
            "base": up.get("base"), "reason": up.get("reason"),
            "unitUsd": up.get("unitUsd"), "cached": up.get("cached"),
            "accessUntil": up.get("accessUntil"),
        },
        "pricing": {"image": IMAGE_CREDITS, "priceUsd": IMAGE_PRICE_USD,
                    "source": IMAGE_PRICE_SOURCE, "videoFree": 0, "agent": 0,
                    "note": "图片 1K/张，单价按上游定价表折算；视频免费档与脚本 Agent 不计积分"},
        "ledger": BILLING.entries(limit),
    }


def billing_precheck(task_id: str, credits: float) -> tuple[bool, dict, int]:
    """提交前检查并冻结积分。返回 (ok, payload, http_status)。

    失败一律 fail-closed：宁可拒绝生成，也不能在账本坏掉时放行免费额度。
    """
    if not BILLING_ENABLED:
        return True, {"ok": True, "billingMode": "off", "skipped": True}, 200
    try:
        snap = billing_snapshot(limit=1)
    except Exception as exc:
        return False, {"ok": False, "error": f"积分账本不可用：{exc}"[:200],
                       "code": "billing_unavailable"}, 503
    if not snap.get("enforce"):
        return True, {"ok": True, "billingMode": "shadow", "skipped": True}, 200
    need = round(float(credits), 2)
    if not snap["unlimited"] and snap["available"] + 1e-9 < need:
        return False, {
            "ok": False, "code": "insufficient_credits",
            "error": f"积分不足：本次需要 {need:g}，可用 {snap['available']:g}",
            "need": need, "available": snap["available"], "billing": snap,
        }, 402
    try:
        res = BILLING.hold(task_id, need, float(snap["base"]), note=f"{snap['source']} 预扣")
    except Exception as exc:
        return False, {"ok": False, "error": f"冻结积分失败：{exc}"[:200],
                       "code": "billing_unavailable"}, 503
    if not res.get("ok"):
        return False, {
            "ok": False, "code": "insufficient_credits",
            "error": f"积分不足：本次需要 {need:g}，可用 {res.get('available', 0):g}",
            "need": need, "available": res.get("available", 0), "billing": snap,
        }, 402
    return True, {"ok": True, "billingMode": snap["billingMode"],
                  "held": res.get("held"), "billing": snap}, 200


def billing_settle(task_id: str, credits: float, mode: str = "upstream") -> None:
    if not BILLING_ENABLED or mode == "shadow":
        return
    try:
        BILLING.settle(task_id, credits, mode=mode)
    except Exception as exc:                     # 结算失败不能把已成功的生成判死
        print(f"[billing] settle {task_id} failed: {exc}", flush=True)


def billing_release(task_id: str, note: str = "释放冻结") -> None:
    if not BILLING_ENABLED:
        return
    try:
        BILLING.release(task_id, note=note)
    except Exception as exc:
        print(f"[billing] release {task_id} failed: {exc}", flush=True)


def append_log(entry: dict) -> None:
    QA.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def redact_headers(headers) -> dict:
    """抓包落盘前脱敏：凭据永不写入证据文件。"""
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk == "authorization":
            out[k] = "Bearer sha256:" + hashlib.sha256(v.encode()).hexdigest()[:12]
        elif lk in ("cookie", "set-cookie", "x-api-key"):
            out[k] = "<redacted>"
        else:
            out[k] = v
    return out


BUILD_FILES = ("index.html", "styles.css", "app.js")


def build_fingerprint() -> dict:
    """页面构建指纹：测试端用它断言「浏览器跑的就是磁盘上的代码」，杜绝缓存假阳性。"""
    out = {}
    for name in BUILD_FILES:
        f = ROOT / name
        if not f.exists():
            out[name] = None
            continue
        data = f.read_bytes()
        out[name] = {"sha256": hashlib.sha256(data).hexdigest()[:16],
                     "bytes": len(data), "mtime": int(f.stat().st_mtime)}
    return out


def append_trace(entry: dict) -> None:
    """L2 抓包：一次 /api/generate 的完整进出链路，append-only，便于三方对账。"""
    QA.mkdir(parents=True, exist_ok=True)
    with TRACE_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------- 上游调用 ----------------

def call_upstream(prompt: str, trace: dict | None = None,
                  images: list[str] | None = None,
                  model: str | None = None) -> tuple[dict, int]:
    """真实调用一次图片模型（默认 flux-3-image）。返回 (结果, HTTP 状态码)。"""
    global _calls, _inflight, _inflight_peak
    # 先抢并发槽（排队等，最多等 IMG_QUEUE_TIMEOUT）：额度检查放到槽里做，
    # 这样「并发上限」与「调用上限」两把闸门不会互相把对方数错。
    if not _img_sem.acquire(timeout=IMG_QUEUE_TIMEOUT):
        return {"ok": False, "error": f"并发已满（上限 {IMG_CONCURRENCY} 个在飞请求），请稍后再试"}, 503
    try:
        with _lock:
            if _calls >= MAX_CALLS:
                return {"ok": False, "error": f"本进程真实调用已达上限 {MAX_CALLS} 次（防止重复计费）"}, 429
            _calls += 1
            call_no = _calls
        with _inflight_lock:
            _inflight += 1
            _inflight_peak = max(_inflight_peak, _inflight)
        return _call_upstream_locked(prompt, trace, images, call_no, model)
    finally:
        with _inflight_lock:
            _inflight -= 1
        _img_sem.release()


def _call_upstream_locked(prompt: str, trace: dict | None,
                          images: list[str] | None, call_no: int,
                          model: str | None = None) -> tuple[dict, int]:
    """真正的上游往返。call_upstream 已经占好并发槽与额度，这里只管发请求。"""

    use_model = model or MODEL
    payload = build_upstream_payload(prompt, images, use_model)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if trace is not None:
        trace["upstream_request"] = {
            "method": "POST", "url": BASE + "/v1/images/generations",
            "headers": {"Content-Type": "application/json", "Accept": "application/json",
                        "User-Agent": "jimeng-clone-local-proxy/1.0",
                        "Authorization": "Bearer sha256:" + hashlib.sha256(("Bearer " + API_KEY).encode()).hexdigest()[:12]},
            "body": payload, "body_bytes": len(body),
        }
        if images:
            # 证据里不放整张图（会把 trace 撑爆），只留数量/大小/指纹
            trace["upstream_request"]["image_summary"] = [
                {"bytes": len(base64.b64decode(u.split(",", 1)[1])),
                 "sha256": hashlib.sha256(base64.b64decode(u.split(",", 1)[1])).hexdigest()[:16]}
                for u in images
            ]
            trace["upstream_request"]["body"] = {k: v for k, v in payload.items() if k != "image"}
            trace["upstream_request"]["body"]["image"] = f"<{len(images)} images omitted>"
    req = urllib.request.Request(
        BASE + "/v1/images/generations",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "jimeng-clone-local-proxy/1.0",
        },
    )
    # 禁用环境代理：带 Authorization 的 POST 在代理下会被静默吞掉。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "call": call_no,
             "model": use_model, "size": FIXED_SIZE, "prompt": prompt[:100],
             "upstream_mode": UPSTREAM_MODE, "base": BASE,
             "trace_id": (trace or {}).get("trace_id")}
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            status = resp.status
        if trace is not None:
            trace["upstream_response"] = {"http_status": status, "body_bytes": len(raw)}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"upstream returned non-JSON (status {status}): {exc}") from exc
        if status >= 400:
            msg = parsed.get("detail") or parsed.get("error") or parsed
            raise RuntimeError(f"upstream HTTP {status}: {json.dumps(msg, ensure_ascii=False)[:400]}")
        url_ref = image_url_of(parsed)
        if url_ref:
            full = url_ref if url_ref.startswith("http://") or url_ref.startswith("https://") \
                else BASE + url_ref
            image = download_image(full, base=BASE)
        else:
            image = extract_image(parsed)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        if trace is not None:
            trace["upstream_response"] = {"http_status": exc.code, "body_head": detail[:200]}
        elapsed = int((time.monotonic() - started) * 1000)
        entry.update(status="error", http_status=exc.code, elapsed_ms=elapsed,
                     error=f"HTTP {exc.code}: {detail}")
        append_log(entry)
        return {"ok": False, "error": f"上游返回 HTTP {exc.code}", "detail": detail[:300],
                "elapsed_ms": elapsed, "call": call_no,
                "upstream_http": exc.code, "error_kind": "http"}, 502
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        if trace is not None:
            trace["upstream_response"] = {"error": str(exc)[:200]}
        entry.update(status="error", elapsed_ms=elapsed, error=str(exc)[:400])
        append_log(entry)
        # 只有真正的网络问题才值得重试；「返回格式不对」重试三次只是多花三次钱
        kind = ("network" if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError))
                else "bad_response")
        msg = str(exc)[:400]
        if kind == "bad_response":
            # 只报「形状」不报内容：data[0] 有哪些键，足够定位是 URL 还是纯文本
            item = ((parsed.get("data") or [{}])[0]
                    if isinstance(parsed, dict) and isinstance(parsed.get("data"), list) else {})
            keys = ",".join(sorted(item)) if isinstance(item, dict) else type(item).__name__
            msg = f"{msg}（data[0] keys={keys}）"
        return {"ok": False, "error": msg, "elapsed_ms": elapsed, "call": call_no,
                "upstream_http": None, "error_kind": kind}, 502

    GENERATED.mkdir(parents=True, exist_ok=True)
    ext = ".png" if image.startswith(b"\x89PNG") else ".jpg"
    name = f"real-{int(time.time() * 1000)}-{call_no}{ext}"
    (GENERATED / name).write_bytes(image)
    if trace is not None:
        trace["artifact"] = {"file": name, "bytes": len(image),
                             "sha256": hashlib.sha256(image).hexdigest()[:16],
                             "magic": "png" if image.startswith(b"\x89PNG") else "jpeg"}
    elapsed = int((time.monotonic() - started) * 1000)
    entry.update(status="success", http_status=status, elapsed_ms=elapsed, bytes=len(image), file=name)
    append_log(entry)
    return {
        "ok": True, "source": "mock" if MOCK_MODE else "real", "refsUsed": len(images or []),
        "upstreamMode": UPSTREAM_MODE, "model": use_model, "size": FIXED_SIZE,
        "images": [{"url": f"/assets/generated/{name}", "bytes": len(image)}],
        "elapsed_ms": elapsed, "call": call_no, "credits": image_credits_for(use_model),
        "usage": parsed.get("usage") if isinstance(parsed, dict) else None,
    }, 200


# ---------------- 任务队列：异步执行 + 重试抖动 + 幂等 ----------------
# 为什么要有它：同步 /api/generate 会一直占着 HTTP 连接等上游，
# 页面想「点完就走、回头再看」就只能自己卡在 await 上；并发也受限于「同时在等的连接数」。
# 这里把「提交」和「执行」拆开：提交只入队（毫秒级返回任务 id），执行由固定 worker 池做。
TRANSIENT_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}

_tasks: "dict[str, dict]" = {}
_tasks_order: "list[str]" = []
_task_lock = threading.Lock()
_task_q: "queue.Queue[dict | None]" = queue.Queue(maxsize=TASK_QUEUE_MAX)
_task_seq = 0
_workers_started = False
_task_stats = {"submitted": 0, "deduped": 0, "retried": 0, "canceled": 0, "rejected": 0}

# 幂等：key -> {"kind": "task"|"result"|"inflight", "task": id, "result": {...}, "ts": ts}
_idem: "dict[str, dict]" = {}
_idem_lock = threading.Lock()


def idem_key_of(raw: object) -> str:
    """把客户端给的幂等键收敛成 32 位指纹；空则返回空串（不启用幂等）。"""
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    if not s or len(s) > 200:
        return ""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


def retry_delay(attempt: int, base: float = None, cap: float = None,
                jitter: float = None, rand: float = None) -> float:
    """第 attempt 次重试前要等多久（attempt 从 0 开始）。

    指数退避 + **full jitter**：delay = random(0, min(cap, base·2^attempt))。
    加抖动的目的不是「让单次更快」，而是避免多个任务在同一毫秒醒来一起打上游
    （重试风暴 / 惊群）。rand 可注入，便于写确定性断言。
    """
    b = TASK_BACKOFF_BASE if base is None else base
    c = TASK_BACKOFF_CAP if cap is None else cap
    j = TASK_JITTER if jitter is None else jitter
    ceiling = min(c, b * (2 ** max(0, attempt))) * max(0.0, j)
    if ceiling <= 0:
        return 0.0
    r = random.random() if rand is None else rand
    return round(ceiling * r, 3)


def is_transient(status: int, result: dict) -> bool:
    """可重试的失败：上游 4xx 里只有 408/409/425/429 与 5xx，以及网络类异常。"""
    if result.get("ok"):
        return False
    http = result.get("upstream_http")
    if isinstance(http, int):
        return http in TRANSIENT_HTTP or http >= 500
    return result.get("error_kind") == "network"


def task_view(task: dict, with_result: bool = True) -> dict:
    """对外视图：不包含 refs（可能几 MB），结果只给 URL 与耗时。"""
    out = {k: task.get(k) for k in
           ("id", "status", "prompt", "model", "refs_count", "attempts", "max_attempts",
            "created", "started", "finished", "error", "error_kind", "upstream_http",
            "next_in_ms", "deduped", "elapsed_ms")}
    out["queue_position"] = 0
    if with_result and task.get("result"):
        out["result"] = task["result"]
    return out


def _prune_tasks() -> None:
    """只保留最近 TASK_KEEP 个；排队中/运行中的永不删。"""
    with _task_lock:
        while len(_tasks_order) > TASK_KEEP:
            for tid in list(_tasks_order):
                t = _tasks.get(tid)
                if t and t["status"] in ("queued", "running"):
                    continue
                _tasks_order.remove(tid)
                _tasks.pop(tid, None)
                if len(_tasks_order) <= TASK_KEEP:
                    break
            else:
                break


def _task_new(prompt: str, refs: list, idem: str, deduped: bool = False,
              model: str | None = None) -> dict:
    global _task_seq
    with _task_lock:
        _task_seq += 1
        tid = f"tk{int(time.time() * 1000)}-{_task_seq}"
        task = {"id": tid, "status": "queued", "prompt": prompt, "refs": refs,
                "model": model or MODEL,
                "refs_count": len(refs or []), "idem": idem, "deduped": deduped,
                "created": datetime.now(timezone.utc).isoformat(),
                "started": None, "finished": None, "attempts": 0,
                "max_attempts": TASK_RETRIES + 1, "error": None, "error_kind": None,
                "upstream_http": None, "next_in_ms": None, "result": None,
                "elapsed_ms": None}
        _tasks[tid] = task
        _tasks_order.append(tid)
    _prune_tasks()
    return task


def submit_task(prompt: str, refs: list, idem_raw: object = "",
                model_raw: object = "") -> tuple[dict, int]:
    """入队并立刻返回。同 idem 键重复提交返回同一个任务，不会多花一次额度。"""
    use_model, model_err = pick_request_model(model_raw)
    if model_err:
        return {"ok": False, "error": model_err}, 400
    key = idem_key_of(idem_raw)
    if key:
        with _idem_lock:
            hit = _idem.get(key)
            if hit and (time.time() - hit["ts"]) <= IDEM_TTL:
                if hit["kind"] == "task":
                    existing = _tasks.get(hit["task"])
                    if existing:
                        _task_stats["deduped"] += 1
                        return {"ok": True, "id": existing["id"], "status": existing["status"],
                                "deduped": True, "task": task_view(existing)}, 200
                elif hit["kind"] == "result":
                    _task_stats["deduped"] += 1
                    return {"ok": True, "id": None, "status": "succeeded", "deduped": True,
                            "replay": True, "result": hit["result"]}, 200
    if not REAL_READY:
        return {"ok": False, "error": "真实生成未启用（缺少凭据或 JIMENG_REAL=0）"}, 503
    if key:
        with _idem_lock:
            _idem[key] = {"kind": "pending", "ts": time.time()}   # 先占位，防并发重复
    task = _task_new(prompt, refs, key, model=use_model)
    ok, binfo, bcode = billing_precheck(task["id"], image_credits_for(use_model))
    if not ok:
        with _task_lock:
            _tasks.pop(task["id"], None)
            try:
                _tasks_order.remove(task["id"])
            except ValueError:
                pass
        if key:
            with _idem_lock:
                _idem.pop(key, None)
        return binfo, bcode
    task["billing_mode"] = binfo.get("billingMode") or "upstream"
    try:
        _task_q.put_nowait(task)
    except queue.Full:
        with _task_lock:
            _tasks.pop(task["id"], None)
            try:
                _tasks_order.remove(task["id"])
            except ValueError:
                pass
        billing_release(task["id"], "队列已满")
        _task_stats["rejected"] += 1
        if key:
            with _idem_lock:
                _idem.pop(key, None)
        return {"ok": False,
                "error": f"任务队列已满（上限 {TASK_QUEUE_MAX}），请稍后再提交"}, 429
    if key:
        with _idem_lock:
            _idem[key] = {"kind": "task", "task": task["id"], "ts": time.time()}
    _task_stats["submitted"] += 1
    _prune_idem()
    return {"ok": True, "id": task["id"], "status": "queued", "deduped": False,
            "queue": task_queue_depth(), "task": task_view(task)}, 202


def _prune_idem() -> None:
    now = time.time()
    with _idem_lock:
        for k in [k for k, v in _idem.items() if now - v["ts"] > IDEM_TTL]:
            _idem.pop(k, None)


def task_queue_depth() -> dict:
    with _task_lock:
        queued = sum(1 for t in _tasks.values() if t["status"] == "queued")
        running = sum(1 for t in _tasks.values() if t["status"] == "running")
    return {"queued": queued, "running": running, "max": TASK_QUEUE_MAX}


def task_stats() -> dict:
    with _task_lock:
        counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "canceled": 0}
        for t in _tasks.values():
            counts[t["status"]] = counts.get(t["status"], 0) + 1
    return {"workers": TASK_WORKERS, "queueMax": TASK_QUEUE_MAX,
            "retries": TASK_RETRIES, "idemTtl": int(IDEM_TTL),
            "counts": counts, **{k: v for k, v in _task_stats.items()}}


def get_task(tid: str) -> dict | None:
    # 注意：_task_lock 不可重入，这里只能进一次锁 —— 排队位也在同一把锁里算完。
    with _task_lock:
        t = _tasks.get(tid)
        if not t:
            return None
        view = task_view(t)
        if t["status"] == "queued":
            order = [x for x in _tasks_order
                     if (_tasks.get(x) or {}).get("status") == "queued"]
            view["queue_position"] = (order.index(tid) + 1) if tid in order else 0
        return view


def cancel_task(tid: str) -> tuple[dict, int]:
    with _task_lock:
        t = _tasks.get(tid)
        if not t:
            return {"ok": False, "error": "任务不存在"}, 404
        if t["status"] in ("succeeded", "failed", "canceled"):
            return {"ok": False, "error": f"任务已 {t['status']}，无法取消"}, 409
        if t["status"] == "running":
            # 在飞的上游请求没法撤回；只标记意图，让它别再重试
            t["cancel_requested"] = True
            _task_stats["canceled"] += 1
            return {"ok": True, "id": tid, "status": "running",
                    "note": "已在跑，不会再重试；本次上游请求无法撤回"}, 200
        t["status"] = "canceled"
        t["finished"] = datetime.now(timezone.utc).isoformat()
        _task_stats["canceled"] += 1
    billing_release(tid, "用户取消")
    return {"ok": True, "id": tid, "status": "canceled"}, 200


def _run_task(task: dict) -> None:
    """执行一个任务：自带重试与抖动；成功/失败都写回 task。"""
    started = time.monotonic()
    with _task_lock:
        task["status"] = "running"
        task["started"] = datetime.now(timezone.utc).isoformat()
    refs = task.pop("refs", None)
    # 派发抖动：多个任务同时入队时不要在同一毫秒一起打上游
    if TASK_DISPATCH_JITTER > 0:
        time.sleep(random.uniform(0, TASK_DISPATCH_JITTER))
    result: dict = {}
    status = 0
    for attempt in range(task["max_attempts"]):
        with _task_lock:
            if task.get("cancel_requested"):
                task["status"] = "canceled"
                task["finished"] = datetime.now(timezone.utc).isoformat()
                task["elapsed_ms"] = int((time.monotonic() - started) * 1000)
                billing_release(task["id"], "取消（未发起上游请求）")
                return
            task["attempts"] = attempt + 1
            if attempt:
                _task_stats["retried"] += 1
        if attempt:
            delay = retry_delay(attempt - 1)
            with _task_lock:
                task["next_in_ms"] = int(delay * 1000)
            time.sleep(delay)
            with _task_lock:
                task["next_in_ms"] = None
        trace = {"trace_id": f"{task['id']}-a{attempt + 1}",
                 "ts": datetime.now(timezone.utc).isoformat(),
                 "upstream_mode": UPSTREAM_MODE,
                 "incoming": {"path": "/api/tasks", "mode": "image",
                              "prompt_len": len(task["prompt"]),
                              "prompt_head": task["prompt"][:80],
                              "prompt_sha256": hashlib.sha256(task["prompt"].encode()).hexdigest()[:16],
                              "refs_count": len(refs or [])}}
        result, status = call_upstream(task["prompt"], trace, refs, task.get("model"))
        result["task"] = task["id"]
        result["attempt"] = attempt + 1
        append_trace(trace)
        if result.get("ok"):
            # 先结算再改 succeeded：轮询方看到成功时，账本必然已扣费
            billing_settle(task["id"], result.get("credits") or IMAGE_CREDITS,
                           task.get("billing_mode") or "upstream")
            with _task_lock:
                task["status"] = "succeeded"
                task["result"] = result
                task["elapsed_ms"] = int((time.monotonic() - started) * 1000)
                task["finished"] = datetime.now(timezone.utc).isoformat()
            return
        with _task_lock:
            task["error"] = result.get("error")
            task["error_kind"] = result.get("error_kind")
            task["upstream_http"] = result.get("upstream_http")
        if not is_transient(status, result) or attempt == task["max_attempts"] - 1:
            break
    billing_release(task["id"], "生成失败")
    with _task_lock:
        task["status"] = "failed"
        task["result"] = result or None
        task["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        task["finished"] = datetime.now(timezone.utc).isoformat()
        if task.get("cancel_requested"):
            task["status"] = "canceled"
        key = task.get("idem")
    if key:
        with _idem_lock:
            _idem.pop(key, None)        # 失败不缓存，允许换个 idem 重提


def _worker_loop() -> None:
    while True:
        task = _task_q.get()
        if task is None:
            _task_q.task_done()
            return
        try:
            _run_task(task)
        except Exception as exc:                      # worker 不能死
            billing_release(task.get("id"), "worker 异常")
            with _task_lock:
                task["status"] = "failed"
                task["error"] = f"worker 异常：{exc}"[:200]
                task["finished"] = datetime.now(timezone.utc).isoformat()
        finally:
            _task_q.task_done()
            _prune_tasks()


def start_workers() -> None:
    global _workers_started
    if _workers_started:
        return
    _workers_started = True
    for i in range(TASK_WORKERS):
        threading.Thread(target=_worker_loop, name=f"img-worker-{i}", daemon=True).start()


# ---------------- HTTP ----------------

# ---------------- 媒体库 / 工具路由的共享助手 ----------------

def _query(path: str) -> dict:
    parsed = urlparse(path)
    return {k: (v[0] if v else "") for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}


def _int_arg(raw, default: int, lo: int, hi: int) -> int:
    try:
        val = int(str(raw))
    except Exception:
        return default
    return max(lo, min(hi, val))


UPLOAD_RE = re.compile(r"^data:(image/(?:png|jpeg|webp)|video/mp4);base64,([A-Za-z0-9+/=\s]+)$")
TOOL_TMP = QA / "tmp"


def _input_media(body: dict) -> tuple[Path | None, str]:
    """把请求里的素材落成一个真实路径。

    支持三种来源，全部经过白名单：
      - file:    /assets/generated/<name> 或 /assets/subjects/<sid>/<file>
      - upload:  data:image/* 或 data:video/mp4 base64（≤24MB 原文）
      - 返回 (path, error)，临时文件由调用方负责清理
    """
    upload = body.get("upload")
    if isinstance(upload, str) and upload.startswith("data:"):
        m = UPLOAD_RE.match(upload)
        if not m:
            return None, "只支持 png/jpeg/webp 图片或 mp4 视频的 data URI"
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", m.group(2)))
        except Exception:
            return None, "base64 解码失败"
        if len(raw) < 64:
            return None, "文件内容为空"
        if len(raw) > 24 * 1024 * 1024:
            return None, "文件过大（上限 24MB）"
        ext = ".mp4" if m.group(1) == "video/mp4" else {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[m.group(1)]
        TOOL_TMP.mkdir(parents=True, exist_ok=True)
        tmp = TOOL_TMP / f"in-{int(time.time() * 1000)}-{os.getpid()}{ext}"
        tmp.write_bytes(raw)
        return tmp, ""
    ref = body.get("file")
    if isinstance(ref, str) and ref.strip():
        ref = ref.strip()
        if ref.startswith("/assets/subjects/"):
            parts = ref[len("/assets/subjects/"):].split("/")
            if len(parts) != 2 or not re.match(r"^[a-z0-9]{6,20}$", parts[0]):
                return None, "主体文件路径非法"
            name = media_lib.safe_name(parts[1])
            if not name:
                return None, "主体文件名非法"
            p = (media_lib.SUBJECTS / parts[0] / name).resolve()
            try:
                p.relative_to((media_lib.SUBJECTS / parts[0]).resolve())
            except ValueError:
                return None, "主体文件路径越界"
            if not p.exists():
                return None, "主体文件不存在"
            return p, ""
        name = media_lib.safe_name(Path(ref).name)
        if not name or ref != f"/assets/generated/{name}":
            return None, "只接受 /assets/generated/ 下的本机产物"
        p = media_lib.media_path(name)
        if not p or not p.exists():
            return None, "文件不存在"
        return p, ""
    return None, "缺少素材：file 或 upload 必填"


def _post_media(path: str, body: dict) -> tuple[dict, int]:
    if path == "/api/library/fav":
        n = body.get("id")
        res = media_lib.set_fav(n, bool(body.get("fav")))
        return res, 200 if res.get("ok") else 400
    if path == "/api/library/trash":
        res = media_lib.trash(body.get("ids"))
        return res, 200 if res.get("ok") else 400
    if path == "/api/library/restore":
        res = media_lib.restore(body.get("ids"))
        return res, 200 if res.get("ok") else 400
    if path == "/api/subjects":
        res = media_lib.save_subject(body)
        return res, 200 if res.get("ok") else 400
    if path == "/api/subjects/delete":
        res = media_lib.delete_subject(body.get("id"))
        return res, 200 if res.get("ok") else 400
    if path == "/api/subjects/restore":
        res = media_lib.restore_subject(body.get("id"))
        return res, 200 if res.get("ok") else 400
    if path.startswith("/api/tools/"):
        kind = path[len("/api/tools/"):]
        src, err = _input_media(body)
        if not src:
            return {"ok": False, "error": err}, 400
        cleanup = str(src).startswith(str(TOOL_TMP))
        try:
            if kind == "crop":
                res = media_tools.crop_image(src, str(body.get("ratio") or "1:1"),
                                             str(body.get("focus") or "center"))
            elif kind == "compress":
                res = media_tools.compress_image(
                    src, _int_arg(body.get("quality"), 82, 30, 95),
                    _int_arg(body.get("maxSide"), 1600, 64, 4096),
                    _int_arg(body.get("targetKb"), 0, 0, 20000))
            elif kind == "frame":
                res = media_tools.extract_frame(src, str(body.get("at") or "first"))
            elif kind == "gif":
                res = media_tools.video_to_gif(
                    src, _int_arg(body.get("fps"), 12, 6, 24),
                    _int_arg(body.get("width"), 480, 160, 960),
                    float(body.get("start") or 0), float(body.get("seconds") or 4))
            else:
                return {"ok": False, "error": "未知工具"}, 404
        finally:
            if cleanup:
                try:
                    src.unlink(missing_ok=True)
                except Exception:
                    pass
        return res, 200 if res.get("ok") else 400
    return {"ok": False, "error": "not found"}, 404


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        if os.environ.get("JIMENG_VERBOSE"):
            # 请求行里可能带 api_key=…（快捷配置链接），日志一律打码
            safe = tuple(
                re.sub(r"(?i)((?:api[_-]?key|apikey|token|key)=)[^&\s\"']+", r"\1***", str(a))
                for a in args)
            super().log_message(fmt, *safe)

    def _json(self, obj: dict, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        query = _query(self.path)

        # 快捷配置：粘贴带 base_url/api_key 的链接 → 写盘 → 302 到干净 URL。
        # 只认本机 Host，避免别人塞一个链接就把你的 key 改走。
        if path in ("/", "/index.html") and is_quick_request(query):
            if not self._local_same_origin():
                return self._json({"ok": False, "error": "仅接受本机同源请求"}, 403)
            result, status = apply_quick_config(query)
            keep = quick_clean_query(query)
            if result.get("ok"):
                applied = result.get("applied") or {}
                keep["_qc"] = "ok"
                keep["_qt"] = str(applied.get("target") or "image")
                keep["_qb"] = str(applied.get("base") or "")
                keep["_qk"] = str(applied.get("keyMasked") or "")
                if applied.get("model"):
                    keep["_qm2"] = str(applied["model"])
            else:
                keep["_qc"] = "err"
                keep["_qm"] = str(result.get("error") or "配置失败")[:140]
            location = path + ("?" + urlencode(keep) if keep else "")
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path == "/api/billing":
            q = _query(self.path)
            force = q.get("force") in ("1", "true", "yes", "on")
            limit = _int_arg(q.get("limit"), 20, 1, 100)
            try:
                snap = billing_snapshot(force=force, limit=limit)
            except Exception as exc:
                return self._json({"ok": False,
                                   "error": f"积分账本不可用：{exc}"[:200]}, 503)
            return self._json(snap)
        if path == "/api/quickcfg":
            if not self._local_same_origin():
                return self._json({"ok": False, "error": "仅接受本机同源请求"}, 403)
            result, status = apply_quick_config(query)
            return self._json(result, status)
        if path == "/api/tasks" or path.startswith("/api/tasks/"):
            if path == "/api/tasks":
                with _task_lock:
                    recent = [_tasks[t] for t in list(_tasks_order)[-20:] if t in _tasks]
                return self._json({"ok": True, "stats": task_stats(),
                                   "queue": task_queue_depth(),
                                   "items": [task_view(t, with_result=False) for t in recent]})
            tid = path[len("/api/tasks/"):].strip("/")
            view = get_task(tid)
            if not view:
                return self._json({"ok": False, "error": "任务不存在"}, 404)
            return self._json({"ok": True, "task": view})
        if path == "/api/status":
            return self._json({
                "real": REAL_READY, "model": MODEL,
                "models": CONFIG.get("models") or [DEFAULT_MODEL], "base": BASE,
                "size": FIXED_SIZE, "resolution": FIXED_RESOLUTION,
                "calls": _calls, "maxCalls": MAX_CALLS,
                "concurrency": IMG_CONCURRENCY,
                "workers": TASK_WORKERS, "queueMax": TASK_QUEUE_MAX,
                "retries": TASK_RETRIES, "jitter": TASK_JITTER,
                "tasks": task_stats(),
                "inflight": _inflight, "inflightPeak": _inflight_peak,
                "keyConfigured": bool(API_KEY), "forcedDemo": not REAL,
                "upstreamMode": UPSTREAM_MODE, "base": BASE,
                "config": public_config(),
                "video": video_info(),
                "reel": {"maxSegments": REEL_MAX_SEG, "size": [REEL_W, REEL_H],
                         "fps": REEL_FPS, "ffmpeg": bool(FFMPEG and Path(FFMPEG).exists())},
                "reelCloud": {"configured": reel_cloud_ready(), "base": REEL_CLOUD_BASE,
                              "keyConfigured": bool(reel_cloud_key()),
                              "timeout": REEL_CLOUD_TIMEOUT},
                "llm": llm_info(),
                "agent": agent_info(),
                "billing": billing_info(),
            })
        if path.startswith("/api/video/"):
            task_id = path[len("/api/video/"):].strip("/")
            result, status = video_status(task_id)
            return self._json(result, status)
        if path == "/api/trace":
            entries = []
            if TRACE_FILE.exists():
                for line in TRACE_FILE.read_text(encoding="utf-8").splitlines()[-200:]:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return self._json({"entries": entries, "total": len(entries)})
        if path == "/api/logs":
            entries = []
            if LOG_FILE.exists():
                for line in LOG_FILE.read_text(encoding="utf-8").splitlines()[-50:]:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return self._json({"entries": entries, "total": len(entries)})
        if path == "/api/health":
            return self._json({"ok": True})
        if path == "/api/build":
            return self._json({"files": build_fingerprint()})
        if path == "/api/config":
            return self._json(public_config())
        if path == "/api/models":
            # 只读探测；失败也返回 200 + ok:false，让页面能展示原因
            return self._json(probe_models(BASE, API_KEY))
        if path == "/api/library":
            q = _query(self.path)
            return self._json(media_lib.scan(
                kind=(q.get("type") or "all").lower(), q=(q.get("q") or "").strip(),
                sort=(q.get("sort") or "new").lower(),
                limit=_int_arg(q.get("limit"), 60, 1, 200),
                offset=_int_arg(q.get("offset"), 0, 0, 100000),
                fav_only=q.get("fav") in ("1", "true", "yes")))
        if path == "/api/library/trash":
            return self._json(media_lib.list_trash())
        if path == "/api/subjects":
            return self._json(media_lib.list_subjects())
        # 静态资源白名单：qa/（含 runtime-config.json 明文密钥）、tools/、server.py 一律不对外
        if not self._static_allowed(path):
            return self._json({"ok": False, "error": "not found"}, 404)
        return super().do_GET()

    def _save_config(self, body: dict) -> None:
        rt = load_runtime_config()
        if "base" in body:
            want = body.get("base")
            if not isinstance(want, str):
                return self._json({"ok": False, "error": "base 必须是字符串"}, 400)
            nb = normalize_image_base(want)
            if want.strip() and not nb:
                return self._json({"ok": False,
                                   "error": "Base URL 不合法：需 http(s)://host[:port]，不带查询串"}, 400)
            if nb and is_self_base(nb):
                return self._json({"ok": False, "error": "Base URL 不能指向本代理自身"}, 400)
            rt["base"] = nb
        if "apiKey" in body:
            k = body.get("apiKey")
            if not isinstance(k, str):
                return self._json({"ok": False, "error": "apiKey 必须是字符串"}, 400)
            k = k.strip()
            if k and (len(k) > 200 or re.search(r"\s", k)):
                return self._json({"ok": False, "error": "API Key 不合法：不可含空白且不超过 200 字符"}, 400)
            if k:
                rt["apiKey"] = k
            elif body.get("clearKey"):
                rt["apiKey"] = ""
        rt["updatedAt"] = datetime.now(timezone.utc).isoformat()
        if rt.get("base") or rt.get("apiKey"):
            save_runtime_config(rt)
        else:
            clear_runtime_config()
        refresh_config()
        return self._json({"ok": True, "config": public_config(),
                           "upstreamMode": UPSTREAM_MODE, "real": REAL_READY})

    @staticmethod
    def _static_allowed(path: str) -> bool:
        p = path.lstrip("/")
        if p in ("", "index.html", "styles.css", "app.js", "views.css", "views.js", "favicon.ico"):
            return True
        return (p.startswith("assets/") and ".." not in p
                and not p.startswith("assets/generated/../"))

    def _local_same_origin(self) -> bool:
        """只允许本机页面（或显式配置的公网站点）改写状态。

        公网白名单走 public_origin_ok：Host + Origin 双重精确匹配，
        避免把「任意网页都能调本地代理」这个洞带到线上。
        """
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
        origin = self.headers.get("Origin") or ""
        if public_origin_ok(host, origin):
            return True
        if host not in ("127.0.0.1", "localhost", "::1"):
            return False
        return origin_allowed(origin, PORT)

    def _read_json_body(self, limit: int):
        length = int(self.headers.get("Content-Length", "0"))
        if length > limit:
            return None, 413, "请求体过大"
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}"), 200, ""
        except Exception as exc:
            return None, 400, f"非法 JSON: {exc}"

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/billing/sync":
            if not self._local_same_origin():
                return self._json({"ok": False, "error": "仅接受本机同源请求"}, 403)
            billing.clear_cache()
            try:
                snap = billing_snapshot(force=True)
            except Exception as exc:
                return self._json({"ok": False,
                                   "error": f"积分账本不可用：{exc}"[:200]}, 503)
            return self._json(snap)
        if path in ("/api/config", "/api/config/test"):
            if not self._local_same_origin():
                return self._json({"ok": False, "error": "仅接受本机同源请求"}, 403)
            body, code, err = self._read_json_body(8 * 1024)
            if body is None:
                return self._json({"ok": False, "error": err}, code)
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "body 必须是对象"}, 400)
            if path == "/api/config/test":
                nb = normalize_base(body.get("base")) or CONFIG["base"]
                nk = body.get("apiKey") if isinstance(body.get("apiKey"), str) and body.get("apiKey") else CONFIG["key"]
                return self._json(probe_models(nb, nk))
            return self._save_config(body)
        if path == "/api/tasks" or path.startswith("/api/tasks/"):
            if not self._local_same_origin():
                return self._json({"ok": False, "error": "仅接受本机同源请求"}, 403)
            if path.startswith("/api/tasks/") and path.endswith("/cancel"):
                tid = path[len("/api/tasks/"):-len("/cancel")].strip("/")
                result, status = cancel_task(tid)
                return self._json(result, status)
            if path != "/api/tasks":
                return self._json({"ok": False, "error": "not found"}, 404)
            body, code, err = self._read_json_body(40 * 1024 * 1024)
            if body is None:
                return self._json({"ok": False, "error": err}, code)
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "body 必须是对象"}, 400)
            prompt = normalize_prompt(body.get("prompt"))
            if not prompt:
                return self._json({"ok": False, "error": "prompt is required"}, 400)
            refs, ref_err = parse_refs(body.get("refs"))
            if ref_err:
                return self._json({"ok": False, "error": ref_err}, 400)
            idem = self.headers.get("Idempotency-Key") or body.get("idem")
            result, status = submit_task(prompt, refs, idem, body.get("model"))
            return self._json(result, status)
        if path == "/api/agent":
            if not self._local_same_origin():
                return self._json({"ok": False, "error": "仅接受本机同源请求"}, 403)
            body, code, err = self._read_json_body(64 * 1024)
            if body is None:
                return self._json({"ok": False, "error": err}, code)
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "body 必须是对象"}, 400)
            result, status = run_agent(body.get("messages"), body.get("context"))
            return self._json(result, status)
        if path in ("/api/script", "/api/shots"):
            if not self._local_same_origin():
                return self._json({"ok": False, "error": "仅接受本机同源请求"}, 403)
            body, code, err = self._read_json_body(64 * 1024)
            if body is None:
                return self._json({"ok": False, "error": err}, code)
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "body 必须是对象"}, 400)
            prompt = normalize_prompt(body.get("prompt"))
            if not prompt:
                return self._json({"ok": False, "error": "prompt is required"}, 400)
            if len(prompt) > LLM_MAX_PROMPT:
                return self._json({"ok": False,
                                   "error": f"prompt 过长（上限 {LLM_MAX_PROMPT} 字）"}, 400)
            if path == "/api/script":
                episodes = body.get("episodes")
                episodes = episodes if isinstance(episodes, int) else 10
                result, status = run_llm_script(prompt, episodes)
            else:
                ep = body.get("ep") if isinstance(body.get("ep"), int) else 1
                result, status = run_llm_shots(prompt, ep, body.get("context"))
            return self._json(result, status)
        if path == "/api/reel":
            if not self._local_same_origin():
                return self._json({"ok": False, "error": "仅接受本机同源请求"}, 403)
            body, code, err = self._read_json_body(64 * 1024)
            if body is None:
                return self._json({"ok": False, "error": err}, code)
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "body 必须是对象"}, 400)
            ep = body.get("ep") if isinstance(body.get("ep"), int) else 1
            if body.get("cloud"):
                result, status = build_reel_cloud(body.get("files"), ep, body.get("options"),
                                                  body.get("lines"), str(body.get("title") or ""))
            else:
                result, status = build_reel(body.get("files"), ep, body.get("options"),
                                            body.get("lines"), str(body.get("title") or ""))
            return self._json(result, status)
        if path == "/api/video":
            if not self._local_same_origin():
                return self._json({"ok": False, "error": "仅接受本机同源请求"}, 403)
            body, code, err = self._read_json_body(12 * 1024 * 1024)
            if body is None:
                return self._json({"ok": False, "error": err}, code)
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "body 必须是对象"}, 400)
            prompt = normalize_prompt(body.get("prompt"))
            if not prompt:
                return self._json({"ok": False, "error": "prompt is required"}, 400)
            # 首帧可选：不传就是纯文生视频；传了必须是本代理认可的 data URI
            image = body.get("image")
            if image in (None, ""):
                image = None
            else:
                if not isinstance(image, str) or not DATA_URI_RE.match(image):
                    return self._json({"ok": False, "error": "image 必须是 data:image/...;base64"}, 400)
                if len(image) > 9 * 1024 * 1024:
                    return self._json({"ok": False, "error": "首帧过大（上限约 9MB base64）"}, 413)
            ratio = body.get("ratio") or "9:16"
            if not isinstance(ratio, str) or ratio not in VIDEO_RATIOS:
                return self._json({"ok": False,
                                   "error": f"ratio 只支持 {'/'.join(sorted(VIDEO_RATIOS))}"}, 400)
            tier = body.get("tier") if body.get("tier") in VIDEO_TIERS else "free"
            result, status = submit_video(prompt, image, tier, ratio)
            return self._json(result, status)
        if path.startswith("/api/library/") or path.startswith("/api/subjects") \
                or path.startswith("/api/tools/"):
            if not self._local_same_origin():
                return self._json({"ok": False, "error": "仅接受本机同源请求"}, 403)
            body, code, err = self._read_json_body(48 * 1024 * 1024)
            if body is None:
                return self._json({"ok": False, "error": err}, code)
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "body 必须是对象"}, 400)
            result, status = _post_media(path, body)
            return self._json(result, status)
        if path != "/api/generate":
            return self._json({"ok": False, "error": "not found"}, 404)
        # 带参考图时请求体会到 MB 级；上限按「10 张 × 单张 6MB + base64 膨胀 + 余量」估
        body_limit = 40 * 1024 * 1024
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > body_limit:
                return self._json({"ok": False,
                                   "error": f"request body too large（上限 {body_limit // (1024 * 1024)}MB）"}, 413)
            incoming = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            return self._json({"ok": False, "error": f"bad request: {exc}"}, 400)

        prompt = normalize_prompt(incoming.get("prompt"))
        mode = incoming.get("mode", "image")
        if not prompt:
            return self._json({"ok": False, "error": "prompt is required"}, 400)
        if mode != "image":
            return self._json({"ok": False, "error": "flux-3-image 仅支持图片生成；视频/Agent 请用演示模式"}, 400)

        refs, ref_err = parse_refs(incoming.get("refs"))
        if ref_err:
            return self._json({"ok": False, "error": ref_err}, 400)
        use_model, model_err = pick_request_model(incoming.get("model"))
        if model_err:
            return self._json({"ok": False, "error": model_err}, 400)
        if not REAL_READY:
            return self._json({"ok": False, "error": "真实生成未启用（缺少凭据或 JIMENG_REAL=0）"}, 503)
        trace = {
            "trace_id": f"t{int(time.time() * 1000)}-{threading.get_ident()}",
            "ts": datetime.now(timezone.utc).isoformat(), "upstream_mode": UPSTREAM_MODE,
            "incoming": {
                "path": path, "headers": redact_headers(self.headers), "body_bytes": length,
                "mode": mode, "prompt_len": len(prompt), "prompt_head": prompt[:80],
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16],
                "refs_count": len(refs),
                "refs_bytes": sum(len(base64.b64decode(u.split(",", 1)[1])) for u in refs),
            },
        }
        idem = idem_key_of(self.headers.get("Idempotency-Key") or incoming.get("idem"))
        if idem:
            with _idem_lock:
                hit = _idem.get(idem)
                if hit and (time.time() - hit["ts"]) <= IDEM_TTL:
                    if hit["kind"] == "result":
                        _task_stats["deduped"] += 1
                        replay = dict(hit["result"], replay=True, deduped=True)
                        return self._json(replay, 200)
                    if hit["kind"] == "inflight":
                        return self._json({"ok": False, "deduped": True,
                                           "error": "重复提交：同一 Idempotency-Key 仍在处理中",
                                           "retryAfterMs": 1500}, 409)
                _idem[idem] = {"kind": "inflight", "ts": time.time()}
        hold_id = f"sync-{trace['trace_id']}"
        ok, binfo, bcode = billing_precheck(hold_id, image_credits_for(use_model))
        if not ok:
            return self._json(binfo, bcode)
        billing_mode = binfo.get("billingMode") or "upstream"
        started = time.monotonic()
        try:
            result, status = call_upstream(prompt, trace, refs or None, use_model)
        except Exception:
            billing_release(hold_id, "同步生成异常")
            raise
        if result.get("ok"):
            billing_settle(hold_id, result.get("credits") or image_credits_for(use_model),
                           billing_mode)
        else:
            billing_release(hold_id, "同步生成失败")
        imgs = result.get("images") or []
        trace["response"] = {
            "status": status, "ok": bool(result.get("ok")), "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": result.get("error"), "call": result.get("call"),
            "refs_used": result.get("refsUsed", 0),
            "image_url": imgs[0].get("url") if imgs else None,
        }
        append_trace(trace)
        if idem:
            with _idem_lock:
                if result.get("ok"):
                    _idem[idem] = {"kind": "result", "result": result, "ts": time.time()}
                else:
                    _idem.pop(idem, None)      # 失败不缓存：允许换个 idem 重试
        return self._json(result, status)

    def do_DELETE(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/api/config":
            return self._json({"ok": False, "error": "not found"}, 404)
        if not self._local_same_origin():
            return self._json({"ok": False, "error": "仅接受本机同源请求"}, 403)
        clear_runtime_config()
        refresh_config()
        return self._json({"ok": True, "config": public_config()})

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        # 开发/自检代理：绝不缓存，且每个响应都带构建指纹，便于测试断言版本一致
        self.send_header("Cache-Control", "no-store, must-revalidate")
        app_js = ROOT / "app.js"
        if app_js.exists():
            self.send_header("X-QA-Build", hashlib.sha256(app_js.read_bytes()).hexdigest()[:12])
        super().end_headers()


def selftest() -> int:
    """确定性纯函数单测：显式规则 + 证伪式断言，零网络。"""
    checks = []

    def chk(name: str, got, expected) -> None:
        ok = got == expected
        checks.append((name, ok, got, expected))

    chk("normalize trims", normalize_prompt("  hello  "), "hello")
    chk("normalize rejects non-str", normalize_prompt(123), "")
    chk("normalize clips 2000", len(normalize_prompt("x" * 3000)), 2000)

    # ---- 配置层：URL 归一化与优先级 ----
    chk("base 去尾斜杠", normalize_base("https://a.com/"), "https://a.com")
    chk("base 去 /v1", normalize_base("https://a.com/v1/"), "https://a.com")
    chk("base 保留端口", normalize_base("http://127.0.0.1:8799/"), "http://127.0.0.1:8799")
    chk("base 拒绝 ftp", normalize_base("ftp://a.com"), "")
    chk("base 拒绝空", normalize_base(""), "")
    chk("base 拒绝非字符串", normalize_base(None), "")
    chk("base 拒绝内嵌凭据", normalize_base("https://u:p@a.com"), "")
    chk("base 拒绝查询串", normalize_base("https://a.com/?x=1"), "")
    chk("mask 短", mask_key("short"), "••••")
    chk("mask 长保留尾4", mask_key("sk-1234567890abcd"), "••••••••abcd")
    chk("mask 空", mask_key(""), "")
    chk("self base 同端口", is_self_base("http://127.0.0.1:8788"), True)
    chk("self base 异端口", is_self_base("http://127.0.0.1:8799"), False)
    chk("页面 base 覆盖 env", pick_base("http://page", "http://env", "http://d"),
        ("http://page", "page"))
    chk("无页面配置用 env", pick_base("", "http://env", "http://d"), ("http://env", "env"))
    chk("都无则默认 base", pick_base("", "", "http://d"), ("http://d", "default"))
    chk("页面 key 最高", pick_key("k-page", "k-env", "k-file", "k-def"), ("k-page", "page"))
    chk("无页面 key 用 env", pick_key("", "k-env", "k-file", "k-def"), ("k-env", "env"))
    chk("再退到 BFL_KEY_FILE", pick_key("", "", "k-file", "k-def"), ("k-file", "env-file"))
    chk("默认文件兜底", pick_key("", "", "", "k-def"), ("k-def", "default-file"))
    chk("全空", pick_key("", "", "", ""), ("", "none"))

    # ---- 快捷配置：基址归一化 / target / 清洗查询串 ----
    # ---- 任务队列：退避抖动 / 可重试分类 / 幂等键 ----
    chk("抖动：rand=0 不等待", retry_delay(0, 0.8, 8, 1.0, rand=0.0), 0.0)
    chk("抖动：rand=1 取上限", retry_delay(0, 0.8, 8, 1.0, rand=1.0), 0.8)
    chk("退避：指数增长", [retry_delay(i, 0.8, 8, 1.0, rand=1.0) for i in range(4)],
        [0.8, 1.6, 3.2, 6.4])
    chk("退避：封顶", retry_delay(10, 0.8, 8, 1.0, rand=1.0), 8.0)
    chk("抖动：jitter=0 关掉随机", retry_delay(2, 0.8, 8, 0.0, rand=1.0), 0.0)
    chk("抖动：半幅", retry_delay(0, 0.8, 8, 0.5, rand=1.0), 0.4)
    chk("抖动：真随机落在区间内",
        all(0 <= retry_delay(3, 0.8, 8, 1.0) <= 6.4 for _ in range(50)), True)
    chk("可重试：502", is_transient(502, {"ok": False, "upstream_http": 502}), True)
    chk("可重试：429", is_transient(429, {"ok": False, "upstream_http": 429}), True)
    chk("可重试：503", is_transient(503, {"ok": False, "upstream_http": 503}), True)
    chk("可重试：网络异常", is_transient(502, {"ok": False, "error_kind": "network"}), True)
    chk("不重试：400", is_transient(502, {"ok": False, "upstream_http": 400}), False)
    chk("不重试：401", is_transient(502, {"ok": False, "upstream_http": 401}), False)
    chk("不重试：成功", is_transient(200, {"ok": True}), False)
    chk("幂等键：同串同指纹", idem_key_of("a-b-c"), idem_key_of(" a-b-c "))
    chk("幂等键：长度 32", len(idem_key_of("x" * 40)), 32)
    chk("幂等键：空串不启用", idem_key_of("   "), "")
    chk("幂等键：超长不启用", idem_key_of("x" * 300), "")
    chk("任务视图不含 refs", "refs" in task_view({"id": "t", "status": "queued", "refs": ["x"]}), False)
    chk("任务视图带排队位", "queue_position" in task_view({"id": "t", "status": "queued"}), True)

    chk("端点 URL 还原成 base", normalize_image_base("https://bfl.1task.cn/v1/images/generations"),
        "https://bfl.1task.cn")
    chk("端点 URL 去掉尾斜杠", normalize_image_base("https://bfl.1task.cn/v1/images/generations/"),
        "https://bfl.1task.cn")
    chk("短端点写法也认", normalize_image_base("https://bfl.1task.cn/images/generations"),
        "https://bfl.1task.cn")
    chk("base 带 /v1 仍去掉", normalize_image_base("https://a.com/v1"), "https://a.com")
    chk("chat 端点 URL 还原", normalize_chat_base("https://api.a.com/v1/chat/completions"),
        "https://api.a.com/v1")
    chk("chat 端点不带 /v1 还原后补上", normalize_chat_base("https://api.a.com/chat/completions"),
        "https://api.a.com/v1")
    chk("chat base 保留 /v1", normalize_chat_base("https://a.com/v1/"), "https://a.com/v1")
    chk("chat base 只有主机时补 /v1", normalize_chat_base("https://a.com"), "https://a.com/v1")
    chk("chat base 保留多段路径", normalize_chat_base("https://a.com/openai/v1/"),
        "https://a.com/openai/v1")
    chk("chat base 拒绝 ftp", normalize_chat_base("ftp://a.com"), "")
    chk("chat base 拒绝查询串", normalize_chat_base("https://a.com/v1?x=1"), "")
    chk("chat base 拒绝内嵌凭据", normalize_chat_base("https://u:p@a.com/v1"), "")
    chk("同源：无 Origin 放过", origin_allowed("", 8788), True)
    chk("同源：本机同端口", origin_allowed("http://127.0.0.1:8788", 8788), True)
    chk("同源：localhost 同端口", origin_allowed("http://localhost:8788", 8788), True)
    chk("同源：跨端口拒绝", origin_allowed("http://127.0.0.1:8788", 8796), False)
    chk("同源：跨站拒绝", origin_allowed("http://evil.example.com:8788", 8788), False)
    chk("同源：https 拒绝", origin_allowed("https://127.0.0.1:8788", 8788), False)
    chk("target 默认 image", quick_target({}), "image")
    chk("target=llm", quick_target({"target": "llm"}), "llm")
    chk("target=both 归到 all", quick_target({"target": "both"}), "all")
    chk("llm=1 等价 target=llm", quick_target({"llm": "1"}), "llm")
    chk("target 非法值", quick_target({"target": "video"}), "invalid")
    chk("识别快捷请求", is_quick_request({"api_key": "k"}), True)
    chk("识别 base 别名", is_quick_request({"api_base": "u"}), True)
    chk("识别 models 快捷参数", is_quick_request({"models": "flux-3-image"}), True)
    chk("models 逗号解析", parse_model_list("flux-3-image, flux-4-image"),
        (["flux-3-image", "flux-4-image"], ""))
    chk("models 去重保序", parse_model_list("a,b,a")[0], ["a", "b"])
    chk("models 空项忽略", parse_model_list("a,,b,")[0], ["a", "b"])
    chk("models 拒绝空白", parse_model_list("bad model")[1] != "", True)
    chk("models 拒绝空数组元素", parse_model_list(["a", 1])[1] != "", True)
    chk("models 上限 20", parse_model_list(",".join(str(i) for i in range(21)))[1] != "", True)
    chk("普通页面参数不算快捷请求", is_quick_request({"view": "drama"}), False)
    chk("别名取值大小写不敏感", quick_get({"API_KEY": " sk-1 "}, QUICK_KEY_KEYS), "sk-1")
    chk("清洗掉密钥与一次性参数", sorted(quick_clean_query(
        {"api_key": "sk-1", "base_url": "https://a", "view": "drama", "_qc": "ok", "theme": "dark"})),
        ["theme", "view"])
    chk("清洗不误伤 model 之外的业务参数", quick_clean_query({"form": "video", "target": "llm"}),
        {"form": "video"})

    # ---- 参考图：校验与请求体构造 ----
    import base64 as _b64
    tiny = _b64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()
    ok_uri = f"data:image/png;base64,{tiny}"
    chk("空 refs 合法", parse_refs(None), ([], ""))
    chk("空数组合法", parse_refs([]), ([], ""))
    chk("单张通过", parse_refs([ok_uri])[1], "")
    chk("单张归一化", parse_refs([ok_uri])[0], [ok_uri])
    chk("接受 {url}", parse_refs([{"url": ok_uri}])[1], "")
    chk("jpg 归一化为 jpeg",
        parse_refs(["data:image/jpg;base64," + tiny])[0][0].startswith("data:image/jpeg;base64,"), True)
    chk("拒绝 http URL", parse_refs(["https://x/y.png"])[1] != "", True)
    chk("拒绝非数组", parse_refs("x")[1], "refs 必须是数组")
    chk("拒绝 11 张", parse_refs([ok_uri] * 11)[1] != "", True)
    chk("10 张通过", parse_refs([ok_uri] * 10)[1], "")
    chk("拒绝坏 base64", parse_refs(["data:image/png;base64,!!!!"])[1] != "", True)
    chk("拒绝空内容", parse_refs(["data:image/png;base64,"])[1] != "", True)
    chk("拒绝 gif", parse_refs(["data:image/gif;base64," + tiny])[1] != "", True)
    big = _b64.b64encode(b"x" * (6 * 1024 * 1024 + 10)).decode()
    chk("拒绝超 6MB", parse_refs([f"data:image/png;base64,{big}"])[1] != "", True)
    chk("无图时不带 image 字段", "image" in build_upstream_payload("p", None), False)
    chk("可覆盖请求模型", build_upstream_payload("p", None, "flux-4-image")["model"], "flux-4-image")
    chk("有图时带 image 数组", build_upstream_payload("p", [ok_uri])["image"], [ok_uri])
    chk("构造体仍钉死 n=1", build_upstream_payload("p", [ok_uri])["n"], 1)
    # ---- 视频：裁首帧 / 构造体 / task id 守卫 ----
    # 用一张真的 64×64 方图做裁切样本（1×1 夹具裁不出 9:16）
    square = b""
    if Image is not None:
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (30, 40, 60)).save(buf, "PNG")
        square = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    frame, size = crop_first_frame(square or ok_uri, (9, 16), 720)
    chk("视频首帧裁成 9:16", bool(size) and abs(size[0] / size[1] - 9 / 16) < 0.01, True)
    chk("视频首帧仍是 data URI", frame.startswith("data:image/"), True)
    vb = build_video_payload("推镜", frame)
    chk("视频模型固定 minimaxH3", vb["model"], AVM_MODEL)
    chk("视频档位钉死 720p/8s", (vb["resolution"], vb["duration"]), ("720p", 8))
    chk("首帧走 first_frame 通道", vb["content"][1]["role"], "first_frame")
    chk("视频比例跟随首帧", vb["ratio"], "adaptive")
    vb2 = build_video_payload("只写提示词", None)
    chk("无首帧时不塞 image_url", [c["type"] for c in vb2["content"]], ["text"])
    chk("无首帧时用显式比例", vb2["ratio"], "9:16")
    chk("无首帧可换横屏", build_video_payload("p", None, "free", "16:9")["ratio"], "16:9")
    chk("非法比例回落竖屏", build_video_payload("p", None, "free", "7:3")["ratio"], "9:16")
    chk("有首帧仍忽略显式比例", build_video_payload("p", frame, "free", "16:9")["ratio"], "adaptive")
    chk("非法 task id 被拒", VIDEO_TASK_RE.match("../etc/passwd"), None)
    chk("正常 task id 通过", bool(VIDEO_TASK_RE.match("cgt-20261108102736-f345fb38")), True)
    o, e = normalize_opts({"transition": 0.3, "subtitles": True, "titles": True, "music": "drone"})
    chk("成片选项解析", (o["transition"], o["subtitles"], o["music"], e), (0.3, True, "drone", ""))
    chk("非法转场被拒", normalize_opts({"transition": 0.9})[1] != "", True)
    chk("非法底音被拒", normalize_opts({"music": "song"})[1] != "", True)
    chk("字幕转义不留花括号", _ass_text("他说{a}并\\n走了"), "他说（a）并＼n走了")
    chk("构造体仍钉死尺寸", build_upstream_payload("p", [ok_uri])["size"], FIXED_SIZE)
    chk("normalize folds blank lines", normalize_prompt("a\n\n\n\nb"), "a\n\nb")

    p = build_upstream_payload("a cat")
    chk("model", p["model"], "flux-3-image")
    chk("size fixed", p["size"], "1024x1024")
    chk("resolution fixed", p["resolution"], "1k")
    chk("n forced 1", p["n"], 1)
    chk("response format", p["response_format"], "b64_json")

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 2048
    chk("extract png", extract_image({"data": [{"b64_json": base64.b64encode(png).decode()}]}), png)
    chk("extract data uri", extract_image({"data": [{"url": "data:image/png;base64," + base64.b64encode(png).decode()}]}), png)
    for name, bad, fragment in [
        ("empty data", {"data": []}, "empty data"),
        ("no payload", {"data": [{}]}, "no image payload"),
        ("url not inline", {"data": [{"url": "https://x/y.png"}]}, "expects inline base64"),
        ("bad base64", {"data": [{"b64_json": "!!!!"}]}, "invalid base64"),
        ("tiny image", {"data": [{"b64_json": base64.b64encode(b"abc").decode()}]}, "too small"),
        ("wrong magic", {"data": [{"b64_json": base64.b64encode(b"x" * 2048).decode()}]}, "neither PNG nor JPEG"),
    ]:
        try:
            extract_image(bad)
            checks.append((name, False, "no exception", fragment))
        except ValueError as exc:
            checks.append((name, fragment in str(exc), str(exc), fragment))

    chk("credit real image", estimate_credits("image", 4, "2K", 0, True), 3.8)
    chk("credit demo image 4x2K", estimate_credits("image", 4, "2K", 0, False), 800)
    chk("credit demo video", estimate_credits("video", 1, "1080P", 5, False), 90)
    chk("credit real video 免费档", estimate_credits("video", 1, "720P", 8, True), 0)
    chk("credit demo agent", estimate_credits("agent", 1, "1K", 0, False), 60)

    # ---- 文本模型：JSON 容错与校验 ----
    chk("解析裸 JSON", parse_json_object('{"a":1}'), {"a": 1})
    chk("解析围栏 JSON", parse_json_object('```json\n{"a":2}\n```'), {"a": 2})
    chk("解析带前后话术", parse_json_object('好的，这是剧本：\n{"a":3}\n希望你喜欢'), {"a": 3})
    chk("解析嵌套", parse_json_object('{"a":{"b":[1,2]}}')["a"]["b"], [1, 2])
    chk("非 JSON 返回 None", parse_json_object("抱歉我不能"), None)
    chk("截断 JSON 返回 None", parse_json_object('{"a":'), None)
    chk("非字符串返回 None", parse_json_object(None), None)
    _shots, _e = normalize_shots([
        {"size": "中景", "desc": "雨夜车库，女人撑伞走向电梯", "line": "（环境声）雨", "dur": 3},
        {"size": "乱写", "desc": "她停下脚步，回头看了一眼", "dur": 99},
        {"size": "特写", "desc": "伞骨上的一滴水落下", "dur": 2},
        {"size": "近景", "desc": "电梯数字跳到 B1", "dur": 2},
        {"size": "过肩", "desc": "门后有人影一闪", "dur": 3},
        {"size": "俯拍", "desc": "整层车库只剩一盏灯", "dur": 4},
    ])
    chk("镜头归一化保留 6 个", len(_shots or []), 6)
    chk("非法景别回落到顺序表", (_shots or [{}])[1]["size"], "全景")
    chk("时长夹在 8s 内", (_shots or [{}])[1]["dur"], 8.0)
    chk("镜号连续", [s["id"] for s in (_shots or [])][:3], ["S01", "S02", "S03"])
    chk("画面描述被保留", (_shots or [{}])[0].get("desc"), "雨夜车库，女人撑伞走向电梯")
    chk("少于 3 镜判失败", normalize_shots([{"desc": "只有一镜的画面"}])[0], None)
    _sc, _err = normalize_script({
        "title": "雨夜归刃", "logline": "她带着旧钥匙回到仇人家门前。",
        "characters": [{"name": "苏念", "desc": "幸存者"}, {"name": "沈砚舟", "desc": "继承人"}],
        "acts": [{"name": "第一幕", "summary": "归城"}],
        "episodes": [{"title": "雨夜归城", "hook": "旧钥匙"}],
        "shots": [{"size": "大全景", "desc": "暴雨夜的城市天际线", "line": "雷声"}],
    }, 10)
    chk("剧本归一化通过", bool(_sc) and _sc["episodes"][0]["n"], 1)
    chk("剧本缺梗概被判失败", normalize_script({"title": "x" * 6}, 10)[1] != "", True)
    chk("剧本人物不足被判失败", normalize_script(
        {"title": "雨夜", "logline": "梗概写够六个字", "characters": [{"name": "A", "desc": "d"}],
         "episodes": [{"title": "一"}]}, 10)[1] != "", True)
    chk("分集不超过请求集数", len(normalize_script({
        "title": "雨夜", "logline": "梗概写够六个字",
        "characters": [{"name": "A", "desc": "d"}, {"name": "B", "desc": "d"}],
        "acts": [{"name": "一", "summary": "s"}],
        "episodes": [{"title": f"第{i}集"} for i in range(12)],
        "shots": []}, 3)[0]["episodes"]), 3)
    chk("单键包裹会解包", _unwrap({"script": {"title": "雨夜"}}), {"title": "雨夜"})
    chk("多层解包只钻两层", _unwrap({"a": {"b": {"c": 1}}}), {"c": 1})
    _zh, _e2 = normalize_script({
        "片名": "旧钥匙", "一句话梗概": "她带着一枚旧钥匙回到仇人的婚宴上。",
        "人物": ["苏念：灭门案唯一幸存者", {"姓名": "沈砚舟", "小传": "沈家独子"}],
        "三幕": [{"名称": "第一幕", "内容": "归城"}],
        "分集": ["婚礼前夜", "旧钥匙"],
        "分镜": [{"景别": "大全景", "画面": "暴雨夜的城市天际线", "台词": "雷声"},
                 {"景别": "中景", "画面": "她把旧钥匙按在桌面上", "台词": "（音效）金属摩擦"},
                 {"景别": "俯拍", "画面": "整层宴会厅只剩一盏灯", "台词": "门合上"}],
    }, 10)
    chk("中文键名也能解析", bool(_zh) and len(_zh["characters"]), 2)
    chk("中文分集按序编号", (_zh or {}).get("episodes", [{}])[0].get("n"), 1)
    chk("中文键名镜头", ((_zh or {}).get("shots") or [{}])[0].get("size"), "大全景")
    chk("分集是字典也能读", normalize_script({
        "title": "雨夜", "logline": "梗概写够六个字", "characters": ["A：一", "B：二"],
        "episodes": {"1": "第一集", "2": "第二集"}}, 10)[0]["episodes"][1]["title"], "第二集")

    globals()["LLM_MODE"] = "off"
    chk("LLM 模式 off 时不就绪", llm_ready(), False)
    globals()["LLM_MODE"] = "auto"

    failed = [c for c in checks if not c[1]]
    for name, ok, got, expected in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got={got!r} expected={expected!r}"))
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
    return 1 if failed else 0


def main() -> int:
    global PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true", help="只跑纯函数单测，零网络")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    PORT = args.port                      # 同源判定 / 自指检测都以实际监听端口为准

    GENERATED.mkdir(parents=True, exist_ok=True)
    QA.mkdir(parents=True, exist_ok=True)
    stale = BILLING.release_stale(float(os.environ.get("JIMENG_BILLING_STALE", "1800") or 1800))
    if stale:
        print(f"[jimeng-clone] 清理孤儿冻结 {stale} 笔", flush=True)
    print(f"[jimeng-clone] http://{HOST}:{args.port}", flush=True)
    print(f"[jimeng-clone] real={REAL_READY} key={'set' if API_KEY else 'missing'} "
          f"model={MODEL} size={FIXED_SIZE} maxCalls={MAX_CALLS}", flush=True)
    if not REAL_READY:
        print("[jimeng-clone] 演示模式：不会发起任何上游生成请求", flush=True)
    start_workers()
    print(f"[jimeng-clone] workers={TASK_WORKERS} queueMax={TASK_QUEUE_MAX} "
          f"retries={TASK_RETRIES} jitter={TASK_JITTER}", flush=True)
    boot_price = refresh_pricing(force=True, timeout=3.0)
    print(f"[jimeng-clone] billing={BILLING_MODE} image={IMAGE_CREDITS:g}积分/张 "
          f"(${IMAGE_PRICE_USD:g} · {IMAGE_PRICE_SOURCE}) db={BILLING.path.name}", flush=True)
    server = ThreadingHTTPServer((HOST, args.port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
