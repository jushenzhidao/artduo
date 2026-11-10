#!/usr/bin/env python3
"""本地确定性图像 / 视频工具：裁切、压缩、抽帧、转 GIF。

这些工具**不调用任何生成模型**，只做像素与容器层面的确定性变换，
所以页面文案统一写「本地处理 · 不消耗积分」。

安全边界：只接受已落盘的输入路径（由 server 校验过的 generated 白名单文件，
或本次上传解码出的临时文件）；输出一律写到 assets/generated/tool-*.{png,jpg,gif}，
文件名由服务端生成，不接受前端指定。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GENERATED = ROOT / "assets" / "generated"
MAX_SIDE = 4096
RATIOS = {
    "1:1": (1, 1), "9:16": (9, 16), "16:9": (16, 9), "4:3": (4, 3),
    "3:4": (3, 4), "3:2": (3, 2), "2:3": (2, 3), "21:9": (21, 9),
}
FOCUS = {"center": (0.5, 0.5), "top": (0.5, 0.18), "bottom": (0.5, 0.82),
         "left": (0.2, 0.5), "right": (0.8, 0.5)}


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def _out(suffix: str) -> Path:
    GENERATED.mkdir(parents=True, exist_ok=True)
    return GENERATED / f"tool-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}{suffix}"


def _run(cmd: list[str], timeout: int = 90) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"处理超时（>{timeout}s）"
    except Exception as exc:
        return False, f"无法执行 ffmpeg：{exc}"
    if p.returncode != 0:
        return False, (p.stderr or "ffmpeg 失败").strip().splitlines()[-1][:300]
    return True, ""


def _require_pil():
    from PIL import Image
    return Image


# ------------------------------------------------------------------ 图片
def crop_image(src: Path, ratio: str, focus: str = "center") -> dict:
    if ratio not in RATIOS:
        return {"ok": False, "error": f"比例只支持 {'/'.join(RATIOS)}"}
    if focus not in FOCUS:
        return {"ok": False, "error": "focus 非法"}
    try:
        Image = _require_pil()
        with Image.open(src) as im:
            im = im.convert("RGB")
            iw, ih = im.size
            rw, rh = RATIOS[ratio]
            target = rw / rh
            # 目标画布取「能覆盖原图的最大框」，避免放大后糊成一片
            if iw / ih > target:
                ch = ih
                cw = round(ih * target)
            else:
                cw = iw
                ch = round(iw / target)
            fx, fy = FOCUS[focus]
            left = max(0, min(iw - cw, round((iw - cw) * fx)))
            top = max(0, min(ih - ch, round((ih - ch) * fy)))
            out = im.crop((left, top, left + cw, top + ch))
            if max(out.size) > MAX_SIDE:
                out.thumbnail((MAX_SIDE, MAX_SIDE))
            dst = _out(".png")
            out.save(dst, "PNG")
            return {"ok": True, "file": dst.name, "url": f"/assets/generated/{dst.name}",
                    "w": out.width, "h": out.height, "bytes": dst.stat().st_size,
                    "from": {"w": iw, "h": ih}, "ratio": ratio, "engine": "PIL"}
    except Exception as exc:
        return {"ok": False, "error": f"裁切失败：{exc}"}


def compress_image(src: Path, quality: int = 82, max_side: int = 1600,
                   target_kb: int = 0) -> dict:
    quality = max(30, min(95, int(quality or 82)))
    max_side = max(64, min(MAX_SIDE, int(max_side or 1600)))
    try:
        Image = _require_pil()
        with Image.open(src) as im:
            im = im.convert("RGB")
            before = (im.width, im.height)
            im.thumbnail((max_side, max_side))
            dst = _out(".jpg")
            q = quality
            im.save(dst, "JPEG", quality=q, optimize=True)
            # 目标体积模式：逐档降质量，最多试 6 次
            if target_kb and target_kb > 0:
                limit = target_kb * 1024
                tries = 0
                while dst.stat().st_size > limit and q > 32 and tries < 6:
                    q -= 10
                    im.save(dst, "JPEG", quality=q, optimize=True)
                    tries += 1
            return {"ok": True, "file": dst.name, "url": f"/assets/generated/{dst.name}",
                    "w": im.width, "h": im.height, "from": {"w": before[0], "h": before[1]},
                    "quality": q, "bytes": dst.stat().st_size, "engine": "PIL"}
    except Exception as exc:
        return {"ok": False, "error": f"压缩失败：{exc}"}


# ------------------------------------------------------------------ 视频
def extract_frame(src: Path, at: str = "first") -> dict:
    if src.suffix.lower() != ".mp4":
        return {"ok": False, "error": "抽帧只支持 mp4"}
    if at in ("first", "0", ""):
        seek = "0"
    elif at == "mid":
        seek = "50%"
    else:
        try:
            sec = max(0.0, min(3600.0, float(at)))
        except Exception:
            return {"ok": False, "error": "时间点非法"}
        seek = f"{sec:.3f}"
    dst = _out(".png")
    ok, err = _run([ffmpeg_bin(), "-y", "-v", "error", "-ss", seek, "-i", str(src),
                    "-frames:v", "1", "-vf", "scale='min(1920,iw)':-2", str(dst)], 60)
    if not ok or not dst.exists() or dst.stat().st_size < 100:
        return {"ok": False, "error": err or "抽帧失败（时间点可能超出片长）"}
    return {"ok": True, "file": dst.name, "url": f"/assets/generated/{dst.name}",
            "at": at, "bytes": dst.stat().st_size, "engine": "ffmpeg"}


def video_to_gif(src: Path, fps: int = 12, width: int = 480,
                 start: float = 0, seconds: float = 4) -> dict:
    if src.suffix.lower() != ".mp4":
        return {"ok": False, "error": "转 GIF 只支持 mp4"}
    fps = max(6, min(24, int(fps or 12)))
    width = max(160, min(960, int(width or 480)))
    start = max(0.0, min(3600.0, float(start or 0)))
    seconds = max(0.5, min(15.0, float(seconds or 4)))
    gen = _out(".gif")
    palette = gen.with_suffix(".palette.png")
    vf = (f"fps={fps},scale={width}:-1:flags=lanczos,"
          f"split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer")
    ok, err = _run([ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{start:.3f}",
                    "-t", f"{seconds:.3f}", "-i", str(src), "-vf", vf,
                    "-loop", "0", str(gen)], 120)
    palette.unlink(missing_ok=True)
    if not ok or not gen.exists() or gen.stat().st_size < 100:
        return {"ok": False, "error": err or "转 GIF 失败"}
    return {"ok": True, "file": gen.name, "url": f"/assets/generated/{gen.name}",
            "bytes": gen.stat().st_size, "fps": fps, "width": width,
            "seconds": seconds, "engine": "ffmpeg"}
