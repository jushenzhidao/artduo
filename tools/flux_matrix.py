#!/usr/bin/env python3
"""flux 网关多形态直连验证（**会计费**，默认逐条执行，失败即停）。

为什么不走本地代理：代理把 `size=1024x1024 / resolution=1k / n=1` 钉死，
任何形态在它后面都长一个样。只有当面对网关，size / resolution / model 才有意义。

形态（每次 1 张，合计 6 次上游调用）：

  M1 基线        flux-3-image      1024x1024  1k    0 参考   直连可用 + 出 1024²
  M2 三图合成    flux-3-image      1024x1024  1k    3 参考   ≥3 张参考这个盲区
  M3 横向 3:2    flux-3-image      1536x1024  1.5k  0 参考   非方形 + 高分辨率档
  M4 竖屏 9:16   flux-3-image      9:16       1k    0 参考   表外比例 1k 应被升 2k
  M5 精确编辑    flux-precise-edit 1024x1024  1k    1 参考   模型切换
  M6 Kontext     flux-kontext-pro  1024x1024  1k    1 参考   另一模型

用法：
  python3 tools/flux_matrix.py                 # 跑全部 6 个
  python3 tools/flux_matrix.py --only M1,M2    # 只跑指定形态
  python3 tools/flux_matrix.py --dry           # 只打印请求体，不发请求（零计费）
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import struct
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "qa" / "shots" / "flux"
OUT.mkdir(parents=True, exist_ok=True)
REPORT = ROOT / "qa" / "flux-matrix.json"

BASE = os.environ.get("FLUX_BASE", "https://bfl.1task.cn").rstrip("/")
KEY = os.environ.get("FLUX_KEY", "sk-bfl")
ENDPOINT = BASE + "/v1/images/generations"


def load_proxy() -> str:
    """该网关本机直连 8.5s、生成直接读超时；bfl2openai 自己是走 socks5h 出去的。

    凭据只从环境或那个 .env 读，不写死、不打印。
    """
    v = os.environ.get("FLUX_PROXY", "").strip()
    if v:
        return v
    env = Path.home() / "PycharmProjects/AI/reverse/bfl/bfl2openai/.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("BFL2OPENAI_FALLBACK_PROXY="):
                return line.split("=", 1)[1].strip()
    return ""


PROXY = load_proxy()

from PIL import Image, ImageDraw  # noqa: E402


def png_data_uri(img: Image.Image, long_side: int = 1280) -> str:
    """与前端同款压缩：长边 1280、JPEG q0.85、透明铺白。"""
    im = img.convert("RGBA")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    bg.paste(im, mask=im.split()[-1])
    im = bg
    scale = min(1.0, long_side / max(im.size))
    if scale < 1.0:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def disc(color: tuple[int, int, int]) -> Image.Image:
    im = Image.new("RGB", (640, 640), (246, 244, 240))
    d = ImageDraw.Draw(im)
    d.ellipse((160, 160, 480, 480), fill=color)
    return im


def triad() -> list[str]:
    """三张高区分度参考：红圆 / 绿方 / 蓝三角，白底。

    用几何体而不是照片，是为了出图后能一眼判定「三个物体是否真的都进来了」——
    照片只靠"看起来像"很容易把模型巧合当成参考生效。
    """
    a = Image.new("RGB", (512, 512), (250, 250, 250))
    ImageDraw.Draw(a).ellipse((96, 96, 416, 416), fill=(200, 30, 30))
    b = Image.new("RGB", (512, 512), (250, 250, 250))
    ImageDraw.Draw(b).rectangle((106, 106, 406, 406), fill=(30, 150, 70))
    c = Image.new("RGB", (512, 512), (250, 250, 250))
    ImageDraw.Draw(c).polygon([(256, 76), (436, 436), (76, 436)], fill=(30, 70, 190))
    return [png_data_uri(a), png_data_uri(b), png_data_uri(c)]


def canvas_dog() -> str:
    """单图编辑的画布：一个明确的场景，方便判断"改了什么"。"""
    im = Image.new("RGB", (1024, 1024), (232, 226, 214))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 640, 1024, 1024), fill=(198, 186, 168))       # 地面
    d.ellipse((300, 420, 720, 760), fill=(120, 92, 66))           # 身体
    d.ellipse((640, 330, 860, 530), fill=(138, 106, 76))          # 头
    d.ellipse((800, 360, 900, 460), fill=(150, 116, 84))          # 口鼻
    d.ellipse((700, 380, 736, 416), fill=(30, 26, 24))            # 眼
    d.polygon([(660, 330), (700, 250), (742, 330)], fill=(112, 84, 60))  # 耳
    return png_data_uri(im)


def post(payload: dict, timeout: int = 300) -> tuple[int, dict, int]:
    body = json.dumps(payload)
    t0 = time.monotonic()
    if PROXY:
        cmd = ["curl", "-s", "--proxy", PROXY, "--max-time", str(timeout),
               "-H", "Content-Type: application/json",
               "-H", f"Authorization: Bearer {KEY}",
               "-H", "Accept: application/json",
               "--data-binary", "@-", "-w", "\n__HTTP__%{http_code}", ENDPOINT]
        try:
            proc = subprocess.run(cmd, input=body, capture_output=True, text=True, timeout=timeout + 20)
            raw, code = proc.stdout, 0
            if "__HTTP__" in raw:
                raw, tail = raw.rsplit("\n__HTTP__", 1)
                code = int(tail.strip() or 0)
            if proc.returncode == 28 and not raw:
                return 0, {"error": f"curl 超时 {timeout}s"}, int((time.monotonic() - t0) * 1000)
        except subprocess.TimeoutExpired:
            return 0, {"error": f"超时 {timeout}s（curl 被 kill）"}, int((time.monotonic() - t0) * 1000)
    else:
        req = urllib.request.Request(ENDPOINT, body.encode(), {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {KEY}",
            "Accept": "application/json",
            "User-Agent": "jimeng-flux-matrix/1.0",
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as r:
                raw, code = r.read().decode("utf-8", "replace"), r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read().decode("utf-8", "replace"), e.code
        except Exception as e:
            return 0, {"error": f"不可达: {e}"}, int((time.monotonic() - t0) * 1000)
    ms = int((time.monotonic() - t0) * 1000)
    try:
        return code, json.loads(raw), ms
    except Exception:
        return code, {"error": raw[:300]}, ms


def png_size(raw: bytes) -> tuple[int, int] | None:
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", raw[16:24])
    return w, h


def build_cases(refs_triad: list[str], canvas: str) -> list[dict]:
    return [
        dict(id="M1", name="基线纯文生图（英文）", model="flux-3-image",
             prompt="A lone red bicycle leaning on a white wall, soft morning light, 35mm film",
             size="1024x1024", resolution="1k", image=None, n=1),
        dict(id="M2", name="三图合成（红圆/绿方/蓝三角）", model="flux-3-image",
             prompt="Compose these three reference objects into one still life: the red circle at the "
                    "upper left, the green square in the center, the blue triangle at the lower right, "
                    "on a light grey table, soft studio light, no text",
             size="1024x1024", resolution="1k", image=refs_triad, n=1),
        dict(id="M3", name="横向 3:2 · 1.5k", model="flux-3-image",
             prompt="Wide cinematic shot of a quiet harbour at dusk, boats and reflections, teal and amber",
             size="1536x1024", resolution="1.5k", image=None, n=1),
        dict(id="M4", name="竖屏 9:16 · 1k（表外比例应升 2k）", model="flux-3-image",
             prompt="Vertical portrait of a ceramic vase with dry branches on linen, soft window light",
             size="9:16", resolution="1k", image=None, n=1),
        dict(id="M5", name="精确编辑（单图）", model="flux-precise-edit",
             prompt="Add a bright red scarf around the dog's neck. Keep everything else unchanged.",
             size="1024x1024", resolution="1k", image=[canvas], n=1),
        dict(id="M6", name="Kontext 编辑（单图）", model="flux-kontext-pro",
             prompt="Replace the background with a snowy forest, keep the dog exactly as it is",
             size="1024x1024", resolution="1k", image=[canvas], n=1),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔的形态 id，如 M1,M4")
    ap.add_argument("--dry", action="store_true", help="只打印请求体，不发请求")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    triad_refs, canvas = triad(), canvas_dog()
    cases = build_cases(triad_refs, canvas)
    want = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    if want:
        cases = [c for c in cases if c["id"] in want]

    print(f"endpoint: {ENDPOINT}")
    print(f"egress: {'socks5h 代理' if PROXY else '直连（慢，可能读超时）'}")
    print(f"key: {KEY[:6]}{'…' if len(KEY) > 6 else ''} · 形态 {len(cases)} 个 · 每个 1 张")
    if args.dry:
        for c in cases:
            body = {k: v for k, v in c.items() if k not in ("id", "name")}
            body["image"] = f"<{len(c['image'])} refs>" if c["image"] else None
            print(json.dumps({c["id"]: body}, ensure_ascii=False))
        return 0

    results = []
    for c in cases:
        payload = {"model": c["model"], "prompt": c["prompt"], "size": c["size"],
                   "resolution": c["resolution"], "n": c["n"], "response_format": "b64_json"}
        if c["image"]:
            payload["image"] = c["image"] if len(c["image"]) > 1 else c["image"][0]
        print(f"\n[{c['id']}] {c['name']} · {c['model']} · {c['size']} · {c['resolution']}"
              f" · refs={len(c['image'] or [])}", flush=True)
        status, data, ms = post(payload, timeout=args.timeout)
        row = {"id": c["id"], "name": c["name"], "model": c["model"], "size": c["size"],
               "resolution": c["resolution"], "refs": len(c["image"] or []),
               "http": status, "ms": ms}
        if status != 200:
            row["error"] = str(data.get("error"))[:400]
            print(f"  FAIL http={status} {row['error'][:160]}", flush=True)
            results.append(row)
            break                       # 失败即停，别继续烧钱
        item = (data.get("data") or [{}])[0]
        b64 = item.get("b64_json")
        if not b64:
            row["error"] = "无 b64_json"
            print("  FAIL 无 b64_json", flush=True)
            results.append(row)
            break
        raw = base64.b64decode(b64)
        size = png_size(raw)
        path = OUT / f"{c['id']}.png"
        path.write_bytes(raw)
        row.update({"bytes": len(raw), "px": list(size) if size else None,
                    "credits": data.get("credits"), "usage": data.get("usage"),
                    "file": str(path.relative_to(ROOT))})
        print(f"  OK {ms}ms · {len(raw)}B · {size} · credits={data.get('credits')}", flush=True)
        results.append(row)

    ok = [r for r in results if r.get("px")]
    print(f"\n{len(ok)}/{len(cases)} 出图 · 报告 qa/flux-matrix.json")
    REPORT.write_text(json.dumps({"endpoint": ENDPOINT, "at": time.strftime("%F %T"),
                                  "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if len(ok) == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
