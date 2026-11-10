#!/usr/bin/env python3
"""零成本的假 avm（方舟 Seedance 协议）：给「出视频」链路做回归。

只实现三条：
  POST /api/v3/contents/generations/tasks        → {"id": "cgt-mock-N"}
  GET  /api/v3/contents/generations/tasks/<id>   → queued ×2 → succeeded(带 video_url)
  GET  /v/test.mp4                               → 现生成的一小段真 mp4（供下载转存）

提示词开关（和 mock_upstream 同一套路）：
  __MOCK_FAIL__   任务直接 failed
  __MOCK_NODL__   成功但 video_url 指向 404 → 测「转存失败」分支
其余按 mock 正常出片。请求体写 qa/mock-avm.jsonl，供测试对账。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
RECORD = QA / "mock-avm.jsonl"
PORT = int(os.environ.get("MOCK_AVM_PORT", "8795"))
# 轮询多少次才出片：默认 2 次（≈8s）；测试「刷新后接续」时调大，让任务在刷新时仍在跑
QUEUED_POLLS = int(os.environ.get("MOCK_AVM_QUEUED", "2"))
MP4 = Path("/tmp/mock-avm.mp4")

_lock = threading.Lock()
_seq = 0
_polls: dict[str, int] = {}


def ensure_mp4() -> None:
    if MP4.exists() and MP4.stat().st_size > 1024:
        return
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=320x568:rate=12:duration=2",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(MP4)],
        check=True, timeout=60)


def frame_size(uri: str) -> list[int] | None:
    m = re.match(r"^data:image/[a-zA-Z0-9.+-]+;base64,(.+)$", uri or "")
    if not m:
        return None
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(base64.b64decode(m.group(1))))
        return list(img.size)
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a) -> None:  # 静音
        pass

    def _json(self, obj: dict, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        global _seq
        path = self.path.split("?", 1)[0]
        if path != "/api/v3/contents/generations/tasks":
            return self._json({"error": {"message": "not found"}}, 404)
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length).decode())
        except Exception:
            return self._json({"error": {"message": "bad json"}}, 400)
        with _lock:
            _seq += 1
            tid = f"cgt-mock-{_seq}"
            _polls[tid] = 0
        content = body.get("content") or []
        text = next((c.get("text", "") for c in content if c.get("type") == "text"), "")
        frame = next((c for c in content if c.get("type") == "image_url"), {})
        entry = {
            "ts": time.strftime("%F %T"), "task": tid, "model": body.get("model"),
            "resolution": body.get("resolution"), "duration": body.get("duration"),
            "ratio": body.get("ratio"), "prompt": text[:120],
            "role": frame.get("role"),
            "frame_bytes": len((frame.get("image_url") or {}).get("url") or ""),
            "frame_size": frame_size((frame.get("image_url") or {}).get("url")),
            "auth": "bearer" if (self.headers.get("Authorization") or "").startswith("Bearer ") else "none",
            "fail": "__MOCK_FAIL__" in text, "nodl": "__MOCK_NODL__" in text,
        }
        QA.mkdir(parents=True, exist_ok=True)
        with RECORD.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return self._json({"id": tid})

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/v/test.mp4":
            ensure_mp4()
            data = MP4.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        m = re.match(r"^/api/v3/contents/generations/tasks/([A-Za-z0-9._-]+)$", path)
        if not m:
            return self._json({"error": {"message": "not found"}}, 404)
        tid = m.group(1)
        with _lock:
            n = _polls.get(tid, 0) + 1
            _polls[tid] = n
        fail = nodl = False
        if RECORD.exists():
            for line in RECORD.read_text(encoding="utf-8").splitlines()[-50:]:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("task") == tid:
                    fail, nodl = rec.get("fail"), rec.get("nodl")
        if fail:
            return self._json({"id": tid, "status": "failed", "error": "mock upstream failed"})
        if n <= QUEUED_POLLS:
            return self._json({"id": tid, "status": "queued"})
        url = f"http://127.0.0.1:{PORT}/v/missing.mp4" if nodl else f"http://127.0.0.1:{PORT}/v/test.mp4"
        return self._json({"id": tid, "status": "succeeded", "duration": 8, "ratio": "9:16",
                           "content": {"video_url": url}})

    def do_DELETE(self) -> None:
        self._json({"ok": True})


def main() -> int:
    ensure_mp4()
    print(f"[mock-avm] http://127.0.0.1:{PORT} record={RECORD}", flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
