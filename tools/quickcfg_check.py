#!/usr/bin/env python3
"""URL 快捷配置自检：?base_url=…&api_key=… 打开即生效，且密钥不留在浏览器/日志里。

自起一个隔离实例（零计费）：

  8794  JIMENG_CONFIG_FILE=/tmp/qc-runtime.json  JIMENG_LLM_CONFIG_FILE=/tmp/qc-llm.json
        JIMENG_MAX_CALLS=0（跑不出图，绝不会花额度）  JIMENG_VERBOSE=1（为了验日志脱敏）

用例分两段：

  A 段（纯 HTTP）
    A1 302 到干净地址，Location 里没有密钥、保留 view
    A2 /api/status 生效：base 归一化（图像去 /v1）、keySource=page、掩码只露尾 4 位
    A3 落盘 qa 侧 JSON 是 0600 且存的是明文（只在本机）
    A4/A5 非法 base、含空白的 key → 302 带回错误，且原配置一字未改
    A6 别名 base/api_base/apikey 都认
    A7/A8 target=llm：写 runtime-llm.json，保留 /v1，只有主机时自动补 /v1
    A9 target=all 两边一起配
    A10 reset=1 清回环境变量
    A11 非本机 Host → 403（别人塞链接改不了你的 key）
    A12 所有响应体都不含明文密钥
    A13 审计日志只有末四位
    A14 访问日志（JIMENG_VERBOSE）里 key 被打码
    A15 全程 0 次上游调用

  B 段（真浏览器）
    B1 地址栏里没有 api_key / _qc
    B2 toast 说清生效的是哪一路、指向哪个 base、密钥尾号
    B3 徽标 title 已是新 base
    B4 刷新后仍是新配置（持久化）
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import video_check
from video_check import B

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
SHOTS = QA / "shots" / "quickcfg"
SHOTS.mkdir(parents=True, exist_ok=True)
video_check.SHOTS = SHOTS
REPORT = QA / "quickcfg-report.json"

PORT = 8794
BASE = f"http://127.0.0.1:{PORT}"
RT_IMG = Path("/tmp/qc-runtime.json")
RT_LLM = Path("/tmp/qc-llm.json")
SERVER_LOG = Path("/tmp/qc-server.log")
AUDIT = QA / "quick-config.jsonl"
MOCK_URI = "http://127.0.0.1:8799"          # dev_up 起的零计费图像上游
MOCK_RECORD = QA / "mock-upstream.jsonl"
QUICK_LOG_MARK = time.strftime("%Y-%m-%dT%H:%M", time.gmtime())

IMG_KEY = "sk-imagetest-000011112222abcd"
LLM_KEY = "sk-llmtest-999988887777wxyz"

checks: list[tuple[str, bool, object, object]] = []
secrets: list[str] = []


def chk(name: str, got, want) -> None:
    checks.append((name, got == want, got, want))
    print(("PASS  " if got == want else "FAIL  ") + name
          + ("" if got == want else f"   got={got!r} want={want!r}"))


def http_get(path: str, follow: bool = False, host: str | None = None) -> tuple[int, dict, bytes]:
    """不跟随跳转，便于断言 302 的 Location。"""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()) if not follow else \
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(BASE + path, headers={"Host": host} if host else {})
    try:
        with opener.open(req, timeout=20) as r:
            body = r.read()
            return r.status, dict(r.headers), body
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_status() -> dict:
    _, _, body = http_get("/api/status", follow=True)
    return json.loads(body)


def api_config() -> dict:
    """public_config：base/baseSource/keySource/keyMasked 都在这。"""
    _, _, body = http_get("/api/config", follow=True)
    return json.loads(body)


def quick(path: str) -> tuple[int, dict]:
    code, _, body = http_get(path, follow=True)
    return code, json.loads(body)


def free_port(port: int) -> None:
    """上一轮残留的监听者会让新实例 EADDRINUSE，而用例会悄悄打到旧代码上。"""
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                         capture_output=True, text=True).stdout
    for pid in out.split():
        subprocess.run(["kill", "-9", pid], capture_output=True)
    time.sleep(0.4)


def spawn() -> subprocess.Popen:
    free_port(PORT)
    for f in (RT_IMG, RT_LLM):
        f.unlink(missing_ok=True)
    Path("/tmp/qc-none.txt").unlink(missing_ok=True)
    env = dict(os.environ, **{
        "PORT": str(PORT),
        "JIMENG_LLM": "auto",
        "LLM_BASE": "https://env-llm.example.com/v1",
        "LLM_MODEL": "env-model",
        "LLM_KEY": "env-llm-key-0000",
        "LLM_KEY_FILE": "/tmp/qc-none.txt",
        "JIMENG_LLM_CONFIG_FILE": str(RT_LLM),
        "JIMENG_CONFIG_FILE": str(RT_IMG),
        "BFL_BASE": "http://127.0.0.1:8799",
        "BFL2OPENAI_API_KEY": "env-img-key-1111",
        "JIMENG_MAX_CALLS": "2",
        "JIMENG_VERBOSE": "1",
        "JIMENG_CONCURRENCY": "2",
    })
    log = SERVER_LOG.open("w")
    proc = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=str(ROOT),
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=1) as r:
                if r.status == 200:
                    return proc
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("8794 起不来")


def file_mode(path: Path) -> str:
    return oct(path.stat().st_mode & 0o777)


def part_a() -> None:
    secrets.extend([IMG_KEY, LLM_KEY])

    # ---------- A1 302 + 干净 Location ----------
    code, headers, body = http_get(
        f"/?theme=dark&view=drama&base_url=https://gw.example.com/v1&api_key={IMG_KEY}"
        "&models=flux-3-image%2Cflux-4-image")
    loc = headers.get("Location", "")
    chk("A1 返回 302", code, 302)
    chk("A1 Location 不含密钥", IMG_KEY in loc or "sk-" in loc, False)
    chk("A1 Location 不含 api_key 参数", "api_key" in loc, False)
    lq = {k: v[0] for k, v in urllib.parse.parse_qs(
        urllib.parse.urlparse(loc).query).items()}
    chk("A1 保留 view/theme", (lq.get("view"), lq.get("theme")), ("drama", "dark"))
    chk("A1 带回成功标记", lq.get("_qc"), "ok")
    chk("A1 标出生效的是图像网关", lq.get("_qt"), "image")
    chk("A1 图像 base 去掉 /v1", lq.get("_qb"), "https://gw.example.com")
    chk("A1 密钥只露尾 4 位", lq.get("_qk"), "••••••••abcd")
    chk("A1 Location 不含 models 一次性参数", "models" in lq, False)
    chk("A1 响应体为空", len(body), 0)

    # ---------- A2 status 生效 ----------
    st, cfg = api_status(), api_config()
    chk("A2 base 生效", st["base"], "https://gw.example.com")
    chk("A2 图像 base 来源标成页面", cfg["baseSource"], "page")
    chk("A2 来源标成页面", cfg["keySource"], "page")
    chk("A2 掩码只露尾四位", cfg["keyMasked"], "••••••••abcd")
    chk("A2 仍算真实链路（有 key）", st["real"], True)
    chk("A2 未发生上游调用", st["calls"], 0)
    chk("A2 models 列表生效", st.get("models"), ["flux-3-image", "flux-4-image"])
    chk("A2 默认模型取第一个", st.get("model"), "flux-3-image")
    chk("A2 models 来源标成页面", cfg.get("modelsSource"), "page")

    # ---------- A3 落盘 ----------
    rt = json.loads(RT_IMG.read_text(encoding="utf-8"))
    chk("A3 磁盘存明文 key", rt["apiKey"], IMG_KEY)
    chk("A3 磁盘存归一化 base", rt["base"], "https://gw.example.com")
    chk("A3 磁盘存 models 列表", rt.get("models"), ["flux-3-image", "flux-4-image"])
    chk("A3 文件权限 0600", file_mode(RT_IMG), "0o600")

    # ---------- A4 非法 base 不落盘 ----------
    code, headers, _ = http_get(f"/?view=drama&base_url=ftp://bad.example.com&api_key={IMG_KEY}")
    loc = headers.get("Location", "")
    chk("A4 非法 base 走 err 分支", "_qc=err" in loc, True)
    chk("A4 报错文案可读", "不合法" in urllib.parse.unquote(loc), True)
    chk("A4 配置未被改动", api_config()["base"], "https://gw.example.com")

    # ---------- A5 key 含空白 ----------
    code, payload = quick("/api/quickcfg?api_key=sk-has%20space&base_url=https://gw.example.com")
    chk("A5 含空白 key 被拒", code, 400)
    chk("A5 拒绝原因", "空白" in (payload.get("error") or ""), True)
    chk("A5 配置未被改动", api_config()["keySource"], "page")
    code, payload = quick("/api/quickcfg?models=bad%20model")
    chk("A5b 非法模型名被拒", code, 400)
    chk("A5b 报错可读", "模型名不合法" in (payload.get("error") or ""), True)
    chk("A5b 模型列表未被改动", api_status().get("models"),
        ["flux-3-image", "flux-4-image"])

    # ---------- A6 别名 ----------
    code, payload = quick("/api/quickcfg?api_base=http://127.0.0.1:8799&apikey=sk-alias-3333")
    chk("A6 别名写法生效", (code, payload["ok"]), (200, True))
    st, cfg = api_status(), api_config()
    chk("A6 base 换成别名给的", st["base"], "http://127.0.0.1:8799")
    chk("A6 别名 key 生效", cfg["keyMasked"].endswith("3333"), True)
    chk("A6 指向本机自动判 mock", st["upstreamMode"], "mock")

    # ---------- A7/A8 target=llm ----------
    code, payload = quick("/api/quickcfg?target=llm&base_url=https://chat.example.com&api_key=" + LLM_KEY)
    chk("A7 llm 快捷配置成功", (code, payload["ok"]), (200, True))
    chk("A7 只有主机时自动补 /v1", payload["llm"]["base"], "https://chat.example.com/v1")
    chk("A7 llm 来源 page", payload["llm"]["baseSource"], "page")
    chk("A7 llm 已就绪", payload["llm"]["configured"], True)
    chk("A7 落盘权限 0600", file_mode(RT_LLM), "0o600")
    rl = json.loads(RT_LLM.read_text(encoding="utf-8"))
    chk("A7 llm 磁盘存明文 key", rl["key"], LLM_KEY)
    chk("A7 图像配置没被动", api_config()["base"], "http://127.0.0.1:8799")

    code, payload = quick("/api/quickcfg?target=llm&base_url=https://chat.example.com/v1/&model=qwen-max")
    chk("A8 带 /v1 不重复拼接", payload["llm"]["base"], "https://chat.example.com/v1")
    chk("A8 模型可单独改", payload["llm"]["model"], "qwen-max")
    chk("A8 key 保持不变", payload["llm"]["keyConfigured"], True)

    # ---------- A9 target=all ----------
    code, payload = quick(f"/api/quickcfg?target=all&base_url=https://both.example.com&api_key={IMG_KEY}")
    chk("A9 两边都写", (payload["config"]["base"], payload["llm"]["base"]),
        ("https://both.example.com", "https://both.example.com/v1"))
    chk("A9 两边来源都是 page", (payload["config"]["baseSource"], payload["llm"]["baseSource"]),
        ("page", "page"))

    # ---------- A10 reset ----------
    code, payload = quick("/api/quickcfg?target=all&reset=1")
    chk("A10 reset 成功", (code, payload["ok"]), (200, True))
    chk("A10 图像回到环境变量", payload["config"]["base"], "http://127.0.0.1:8799")
    chk("A10 图像来源回到 env", payload["config"]["baseSource"], "env")
    chk("A10 llm 回到环境变量", payload["llm"]["base"], "https://env-llm.example.com/v1")
    chk("A10 llm 模型回到环境变量", payload["llm"]["model"], "env-model")
    chk("A10 llm 文件已删", RT_LLM.exists(), False)

    # ---------- A11 非本机 Host ----------
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    conn.request("GET", f"/?api_key={IMG_KEY}&base_url=https://evil.example.com",
                 headers={"Host": "evil.example.com"})
    resp = conn.getresponse()
    chk("A11 外部 Host 被拒", resp.status, 403)
    resp.read()
    conn.close()
    chk("A11 外部写入没生效", api_config()["baseSource"], "env")

    # ---------- A12 响应体不含明文 ----------
    leaks = []
    for path in ("/api/status", "/api/config",
                 f"/api/quickcfg?base_url=https://gw.example.com&api_key={IMG_KEY}",
                 "/?base_url=https://gw.example.com&api_key=" + IMG_KEY):
        _, _, b = http_get(path, follow=False)
        text = b.decode("utf-8", "replace")
        for s in secrets:
            if s in text:
                leaks.append((path, s[:8]))
    chk("A12 所有响应体不含明文密钥", leaks, [])

    # ---------- A13 审计日志 ----------
    audit_raw = AUDIT.read_text(encoding="utf-8") if AUDIT.exists() else ""
    lines = [json.loads(l) for l in audit_raw.splitlines() if l.strip()]
    recent = [l for l in lines if l.get("ts", "") >= QUICK_LOG_MARK]
    chk("A13 有审计记录", len(recent) > 0, True)
    chk("A13 审计只记尾四位", any(l.get("keyTail") == "abcd" for l in recent), True)
    raw = AUDIT.read_text(encoding="utf-8")
    chk("A13 审计不含明文", any(s in raw for s in secrets), False)

    # ---------- A14 访问日志脱敏 ----------
    time.sleep(0.4)
    log_text = SERVER_LOG.read_text(encoding="utf-8", errors="replace")
    chk("A14 访问日志确有记录", "GET /" in log_text, True)
    chk("A14 日志里密钥被打码", any(s in log_text for s in secrets), False)
    chk("A14 打码标记存在", "key=***" in log_text or "api_key=***" in log_text, True)

    # ---------- A15 到此零上游调用 ----------
    chk("A15 到目前零上游调用", api_status()["calls"], 0)

    # ---------- A16 完整端点 URL（用户从文档复制的那种） ----------
    code, payload = quick(
        "/api/quickcfg?base_url=https://bfl.1task.cn/v1/images/generations&api_key=" + IMG_KEY)
    chk("A16 端点是完整 URL 也能受理", (code, payload["ok"]), (200, True))
    chk("A16 剥掉端点只剩 host", payload["config"]["base"], "https://bfl.1task.cn")
    code, payload = quick(
        "/api/quickcfg?target=llm&base_url=https://api.example.com/v1/chat/completions&api_key=" + LLM_KEY)
    chk("A16 chat 端点 URL 还原并保留 /v1", payload["llm"]["base"], "https://api.example.com/v1")

    # ---------- A17 上游实际收到的就是「那两行」 ----------
    marker = f"快捷配置转发校验 {time.time()}"
    quick("/api/quickcfg?base_url=" + MOCK_URI + "/v1/images/generations&api_key=sk-bfl")
    st = api_status()
    chk("A17 base 指向本机 mock", st["base"], MOCK_URI)
    cfg17 = api_config()
    chk("A17 页面 key 已生效", cfg17["keySource"], "page")
    # sk-bfl 只有 6 位 → 掩码不给尾四位，避免把整个 key 露出来
    chk("A17 短 key 掩码不露尾", cfg17["keyMasked"], "••••")
    body = json.dumps({"prompt": marker}).encode()
    req = urllib.request.Request(BASE + "/api/generate", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=120) as r:
        gen = json.loads(r.read())
    chk("A17 出图成功（说明路径没拼成两遍）", gen.get("ok"), True)
    chk("A17 只花了 1 次额度", api_status()["calls"], 1)
    last = None
    for line in MOCK_RECORD.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if marker in json.dumps(row.get("body") or {}, ensure_ascii=False):
            last = row
    chk("A17 上游收到 POST /v1/images/generations", (last or {}).get("path"), "/v1/images/generations")
    want_auth = "Bearer sha256:" + hashlib.sha256(b"Bearer sk-bfl").hexdigest()[:12]
    chk("A17 上游收到的 Authorization 是 Bearer sk-bfl",
        ((last or {}).get("headers") or {}).get("Authorization"), want_auth)
    chk("A17 上游 key 未落明文", "sk-bfl" in MOCK_RECORD.read_text(encoding="utf-8"), False)


def part_b() -> None:
    b = B()
    try:
        url = (f"{BASE}/?theme=dark&view=drama&case=qc-ui"
               f"&base_url=http://127.0.0.1:8799&api_key=sk-uitest-5566778899")
        secrets.append("sk-uitest-5566778899")
        d = video_check.jl(video_check.sh("navigate", "--session", b.s, "--tab-id",
                                          str(b.tab), url, "--json", timeout=60))
        if d.get("reached") != "load":
            b.act("navigate", url)
        chk("B1 页面就绪", b.wait_ready(), True)
        time.sleep(1.2)

        search = b.ev("location.search") or ""
        chk("B1 地址栏已无 api_key", "api_key" in search or "sk-uitest" in search, False)
        chk("B1 一次性参数已抹掉", "_qc" in search, False)
        chk("B1 业务参数保留", "view=drama" in search, True)

        toast = b.ev("document.querySelector('#toasts .toast span')?.textContent || ''") or ""
        chk("B2 toast 说明生效对象", "图像网关" in toast, True)
        chk("B2 toast 带新 base", "127.0.0.1:8799" in toast, True)
        chk("B2 toast 带密钥尾号", "8899" in toast, True)
        chk("B2 toast 不含明文", "sk-uitest" in toast, False)

        title = b.ev("document.querySelector('#apiBadge')?.title || ''") or ""
        chk("B3 徽标指向新 base", "127.0.0.1:8799" in title, True)
        chk("B3 徽标标为 mock 链路", "MOCK" in title.upper(), True)

        b.act("screenshot", "--out", str(SHOTS / "01-applied.png"))

        # 刷新：配置已经在服务端，换个干净地址也应指向新 base
        clean = f"{BASE}/?theme=dark&view=drama&case=qc-reload"
        d = video_check.jl(video_check.sh("navigate", "--session", b.s, "--tab-id",
                                          str(b.tab), clean, "--json", timeout=60))
        if d.get("reached") != "load":
            b.act("navigate", clean)
        chk("B4 重开仍就绪", b.wait_ready(), True)
        title2 = b.ev("document.querySelector('#apiBadge')?.title || ''") or ""
        chk("B4 刷新后仍是新 base", "127.0.0.1:8799" in title2, True)
        chk("B4 无多余提示", b.ev("!!document.querySelector('#toasts .toast')"), False)
    finally:
        subprocess.run(["bsk", "session", "stop", "--session", b.s, "--json"],
                       capture_output=True)


def main() -> int:
    proc = spawn()
    try:
        part_a()
        try:
            part_b()
        except Exception as exc:                      # 浏览器段失败也要留下 A 段结论
            chk("B 段运行", f"{type(exc).__name__}: {exc}"[:120], "ok")
    finally:
        # 收尾：清掉测试写入的配置，别影响别的实例/后续运行
        for f in (RT_IMG, RT_LLM):
            f.unlink(missing_ok=True)
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    passed = sum(1 for c in checks if c[1])
    REPORT.write_text(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "passed": passed, "total": len(checks),
        "items": [{"name": n, "ok": ok, "got": g, "want": w} for n, ok, g, w in checks],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{passed}/{len(checks)} passed -> {REPORT.relative_to(ROOT)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
