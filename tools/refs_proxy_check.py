#!/usr/bin/env python3
"""参考图代理层对抗探针：只打本机代理 + mock 上游，零计费。

判定不只看状态码：每个非法请求都必须 400，且**上游计数不增加**（没被转发出去）。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROXY = os.environ.get("REFS_PROXY", "http://127.0.0.1:8791")
MOCK = os.environ.get("REFS_MOCK", "http://127.0.0.1:8799")
QA = Path(__file__).resolve().parent.parent / "qa"
REF = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
       "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def upstream_n() -> int:
    with op.open(MOCK + "/__stats", timeout=5) as r:
        return int(json.loads(r.read())["requests"])


def post(payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(PROXY + "/api/generate", body, {"Content-Type": "application/json"})
    try:
        with op.open(req, timeout=90) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def main() -> int:
    results = []

    def probe(name: str, payload: dict, expect_status: int = 400) -> None:
        n0 = upstream_n()
        st, body = post(payload)
        delta = upstream_n() - n0
        ok = st == expect_status and delta == 0
        results.append({"name": name, "status": st, "upstream_delta": delta,
                        "error": body.get("error"), "ok": ok})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: HTTP {st} · 上游Δ {delta} · {str(body.get('error'))[:44]}")

    probe("refs 非数组", {"prompt": "x", "refs": "http://a"})
    probe("http URL 被拒", {"prompt": "x", "refs": ["https://example.com/a.png"]})
    probe("gif 被拒", {"prompt": "x", "refs": ["data:image/gif;base64,R0lGODlhAQABAAAAACw="]})
    probe("11 张超限", {"prompt": "x", "refs": [REF] * 11})
    probe("坏 base64", {"prompt": "x", "refs": ["data:image/png;base64,!!!!"]})
    probe("空 base64", {"prompt": "x", "refs": ["data:image/png;base64,"]})
    probe("非图片 mime", {"prompt": "x", "refs": ["data:text/plain;base64,aGk="]})
    probe("缺 url 字段", {"prompt": "x", "refs": [{"name": "a"}]})
    big = "data:image/png;base64," + base64.b64encode(os.urandom(6 * 1024 * 1024 + 16)).decode()
    probe("单张超 6MB", {"prompt": "x", "refs": [big]})

    rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "proxy": PROXY, "mock": MOCK,
           "passed": sum(1 for r in results if r["ok"]), "total": len(results), "probes": results}
    QA.mkdir(parents=True, exist_ok=True)
    (QA / "refs-proxy-report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                               encoding="utf-8")
    print(f"\n{rep['passed']}/{rep['total']} passed -> qa/refs-proxy-report.json")
    return 0 if rep["passed"] == rep["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
