#!/usr/bin/env python3
"""配置层端到端测试：优先级、热生效、校验、同源、密钥不下发。

自己拉起临时实例，跑完即杀，不污染常驻服务。
  - 8790：不设任何 BFL_* 环境变量 → 页面配置可写
  - 8791：只设 BFL_BASE          → base 锁定、key 仍可写（部分锁定）
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "qa"
OUT = QA / "config-report.json"
CFG = Path("/tmp/jimeng-cfg-test.json")   # 通过 JIMENG_CONFIG_FILE 注入，不碰常驻配置
HOST = "127.0.0.1"
MOCK = "http://127.0.0.1:8799"
FAKE_KEY = "sk-test-abcdefghijklmnop-9f2c"


def call(port: int, method: str, path: str, body=None, headers=None):
    c = http.client.HTTPConnection(HOST, port, timeout=25)
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    payload = json.dumps(body).encode() if body is not None else None
    c.request(method, path, body=payload, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw), raw.decode("utf-8", "replace")
    except json.JSONDecodeError:
        return r.status, None, raw.decode("utf-8", "replace")


def mock_gen_count() -> int:
    """mock 只统计 POST /v1/images/generations，用它证明「只读探测不发生成」。"""
    try:
        c = http.client.HTTPConnection("127.0.0.1", 8799, timeout=3)
        c.request("GET", "/__stats")
        n = int(json.loads(c.getresponse().read()).get("requests", -1))
        c.close()
        return n
    except Exception:
        return -1


def wait_port(port: int, timeout=12.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            s = socket.create_connection((HOST, port), timeout=0.25)
            s.close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


def spawn(port: int, env_extra: dict) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items()
           if k not in ("BFL_BASE", "BFL_KEY_FILE", "BFL2OPENAI_API_KEY")}
    env.update({"PORT": str(port), "JIMENG_MAX_CALLS": "50",
                "JIMENG_CONFIG_FILE": str(CFG)})
    env.update(env_extra)
    p = subprocess.Popen([sys.executable, str(ROOT / "server.py")], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_port(port):
        p.kill()
        raise RuntimeError(f"端口 {port} 未就绪")
    return p


def main() -> int:
    results = []

    def chk(name, got, expected):
        ok = got == expected
        results.append({"name": name, "ok": ok, "got": got, "expected": expected})
        print(f"  {'PASS' if ok else 'FAIL'} {name}" + ("" if ok else f"  got={got!r} want={expected!r}"))

    try:
        CFG.unlink()
    except FileNotFoundError:
        pass

    p1 = spawn(8790, {})
    p2 = None
    p3 = None
    try:
        # ---------- 1. 默认态：无环境变量 ----------
        st, cfg, raw = call(8790, "GET", "/api/config")
        chk("默认 base", cfg["base"], "https://bfl.1task.cn")
        chk("默认 baseSource", cfg["baseSource"], "default")
        chk("默认无 env base", cfg["envBase"], "")
        chk("默认无页面 base", cfg["pageBase"], "")
        chk("默认 key 已配置（本机 .env）", cfg["keyConfigured"], True)
        chk("默认 key 走默认文件", cfg["keySource"], "default-file")
        chk("响应不含明文 key", FAKE_KEY in raw, False)

        # ---------- 2. 保存页面配置 ----------
        st, saved, raw = call(8790, "POST", "/api/config",
                              {"base": MOCK + "/v1/", "apiKey": FAKE_KEY})
        chk("保存成功", (st, saved.get("ok")), (200, True))
        chk("base 归一化（去 /v1 与尾斜杠）", saved["config"]["base"], MOCK)
        chk("baseSource 变 page", saved["config"]["baseSource"], "page")
        chk("keySource 变 page", saved["config"]["keySource"], "page")
        chk("key 掩码只留尾4", saved["config"]["keyMasked"], "••••••••9f2c")
        chk("保存响应不含明文 key", FAKE_KEY in raw, False)

        # ---------- 3. 落盘与权限 ----------
        chk("配置文件存在", CFG.exists(), True)
        mode = stat.S_IMODE(CFG.stat().st_mode) if CFG.exists() else 0
        chk("配置文件权限 600", oct(mode), "0o600")
        disk = json.loads(CFG.read_text(encoding="utf-8"))
        chk("磁盘上有 key（页面不回传，但本机需要）", disk.get("apiKey"), FAKE_KEY)

        # ---------- 4. 热生效：status 与真实生成 ----------
        st, status, _ = call(8790, "GET", "/api/status")
        chk("status.base 已切换", status["base"], MOCK)
        chk("status 变 mock 链路", status["upstreamMode"], "mock")
        chk("status.real 就绪", status["real"], True)

        st, gen, _ = call(8790, "POST", "/api/generate", {"prompt": "配置热生效验证", "mode": "image"})
        chk("生成走通（未重启进程）", (st, gen.get("ok")), (200, True))
        chk("生成来源为 mock", gen.get("source"), "mock")
        chk("产物可访问", str(gen.get("images", [{}])[0].get("url", "")).startswith("/assets/generated/"), True)

        # ---------- 5. 测试连接（只读 /v1/models，不得触发生成） ----------
        gen_before = mock_gen_count()
        st, probe, _ = call(8790, "POST", "/api/config/test", {})
        gen_after = mock_gen_count()
        chk("测试连接成功", (st, probe.get("ok")), (200, True))
        chk("探测到 flux-3-image", probe.get("hasModel"), True)
        chk("测试连接零生成请求", (gen_before, gen_after), (gen_before, gen_before))

        # ---------- 5b. 只读模型列表 ----------
        gen_before = mock_gen_count()
        st, models, _ = call(8790, "GET", "/api/models")
        chk("模型列表 HTTP 200", st, 200)
        chk("模型列表 ok", models.get("ok"), True)
        chk("模型列表含 flux-3-image", models.get("hasModel"), True)
        chk("模型列表有内容", models.get("count", 0) >= 1, True)
        chk("模型列表不触发生成", mock_gen_count(), gen_before)

        # ---------- 6. 校验与守卫 ----------
        st, bad, _ = call(8790, "POST", "/api/config", {"base": "ftp://x.com"})
        chk("拒绝非法协议", st, 400)
        st, bad, _ = call(8790, "POST", "/api/config", {"base": "http://127.0.0.1:8790"})
        chk("拒绝指向自身", (st, "自身" in bad.get("error", "")), (400, True))
        st, bad, _ = call(8790, "POST", "/api/config", {"base": "https://u:p@x.com"})
        chk("拒绝内嵌凭据", st, 400)
        st, bad, _ = call(8790, "POST", "/api/config", {"apiKey": "has space"})
        chk("拒绝含空白的 key", st, 400)
        st, bad, _ = call(8790, "POST", "/api/config", {"apiKey": "x" * 300})
        chk("拒绝超长 key", st, 400)
        st, bad, _ = call(8790, "POST", "/api/config", {"base": "https://ok.example.com"},
                          {"Origin": "https://evil.example.com"})
        chk("跨源写入被拒", st, 403)
        st, bad, _ = call(8790, "POST", "/api/config", {"base": "https://ok.example.com"},
                          {"Origin": "http://127.0.0.1:8790"})
        chk("同源写入放行", st, 200)

        # ---------- 7. 清除 ----------
        st, cleared, _ = call(8790, "DELETE", "/api/config")
        chk("清除成功", (st, cleared.get("ok")), (200, True))
        chk("清除后回到默认 base", cleared["config"]["baseSource"], "default")
        chk("配置文件已删除", CFG.exists(), False)

        # ---------- 9. 静态白名单：源码与 qa/ 不可通过 HTTP 读走 ----------
        decoy = QA / "leak-probe.txt"
        decoy.write_text("SECRET-SHOULD-NOT-LEAK", encoding="utf-8")
        try:
            for path, want in [("/", 200), ("/app.js", 200), ("/assets/art-01.svg", 200),
                               ("/server.py", 404), ("/qa/trace.jsonl", 404),
                               ("/qa/leak-probe.txt", 404), ("/tools/e2e_bsk.py", 404)]:
                st, _, raw = call(8790, "GET", path)
                chk(f"静态白名单 {path} -> {want}", st, want)
                if want == 404:
                    chk(f"静态白名单 {path} 无内容", "SECRET-SHOULD-NOT-LEAK" in raw, False)
        finally:
            decoy.unlink(missing_ok=True)

        # ---------- 8. 页面配置优先于部署环境变量 ----------
        p2 = spawn(8791, {"BFL_BASE": MOCK})
        st, cfg2, _ = call(8791, "GET", "/api/config")
        chk("无页面配置时用 env base", (cfg2["baseSource"], cfg2["base"]), ("env", MOCK))
        chk("envBase 回显给前端", cfg2["envBase"], MOCK)
        st, r, _ = call(8791, "POST", "/api/config", {"base": "https://page-wins.example.com"})
        chk("页面 base 覆盖 env", (st, r["config"]["baseSource"]), (200, "page"))
        chk("生效 base 为页面值", r["config"]["base"], "https://page-wins.example.com")
        st, r, _ = call(8791, "POST", "/api/config", {"base": ""})
        chk("清空页面 base 回退 env",
            (st, r["config"]["baseSource"], r["config"]["base"]), (200, "env", MOCK))

        p3 = spawn(8792, {"BFL2OPENAI_API_KEY": "sk-env-priority-0001"})
        st, cfg3, _ = call(8792, "GET", "/api/config")
        chk("无页面 key 时用 env", (cfg3["keySource"], cfg3["keyMasked"]), ("env", "••••••••0001"))
        st, r, _ = call(8792, "POST", "/api/config", {"apiKey": FAKE_KEY})
        chk("页面 key 覆盖 env",
            (r["config"]["keySource"], r["config"]["keyMasked"]), ("page", "••••••••9f2c"))
        st, r, _ = call(8792, "POST", "/api/config", {"apiKey": "", "clearKey": True})
        chk("清空页面 key 回退 env",
            (r["config"]["keySource"], r["config"]["keyMasked"]), ("env", "••••••••0001"))
    finally:
        for proc in (p1, p2, p3):
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        try:
            CFG.unlink()
        except FileNotFoundError:
            pass

    passed = sum(1 for r in results if r["ok"])
    QA.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "total": len(results), "passed": passed,
                               "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[cfg] {passed}/{len(results)} PASS -> {OUT}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
