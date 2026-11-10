#!/usr/bin/env python3
"""把常驻服务交给 macOS launchd 托管（页面 + 两个 mock 上游）。

为什么不用 nohup：由 shell 派生的后台进程会在父进程被回收时一起收到 SIGTERM，
表现就是「服务动不动就 143」。launchd 是系统级管理器，进程挂在 launchd 下，
既不受 shell 生命周期影响，KeepAlive 还能在崩溃后自动拉起。

    python3 tools/launchd.py install     # 写 plist + 加载（已在跑会先卸）
    python3 tools/launchd.py status      # 看 launchd 里的状态与端口
    python3 tools/launchd.py uninstall   # 卸掉并删 plist
    python3 tools/launchd.py restart NAME

服务清单写死在 SERVICES 里，端口与命令跟 tools/dev_up.sh 保持一致。
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = Path.home() / "Library" / "LaunchAgents"
LOGDIR = Path(os.environ.get("TMPDIR", "/tmp")) / "jimeng-dev"
PYTHON = sys.executable
DOMAIN = f"gui/{os.getuid()}"

# name -> (label 后缀, 脚本, 端口, 额外环境变量)
SERVICES = {
    "page": ("page", "server.py", 8788, {"JIMENG_MAX_CALLS": "50"}),
    "mockflux": ("mockflux", "tools/mock_upstream.py", 8799, {"MOCK_PORT": "8799"}),
    "mockavm": ("mockavm", "tools/mock_avm.py", 8795, {}),
}
PREFIX = "cn.1task.jimeng"


def label_of(name: str) -> str:
    return f"{PREFIX}.{name}"


def plist_path(name: str) -> Path:
    return AGENTS / f"{label_of(name)}.plist"


def build(name: str) -> dict:
    _, script, port, extra = SERVICES[name]
    env = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "PYTHONUNBUFFERED": "1",
        **extra,
    }
    log = LOGDIR / f"launchd-{port}.log"
    return {
        "Label": label_of(name),
        "ProgramArguments": [PYTHON, str(ROOT / script), "--port", str(port)]
        if script == "server.py" else [PYTHON, str(ROOT / script)],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        # 崩了别疯狂重启：10s 一次
        "ThrottleInterval": 10,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def run(*args: str) -> tuple[int, str]:
    p = subprocess.run(args, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def bootout(name: str) -> None:
    run("launchctl", "bootout", f"{DOMAIN}/{label_of(name)}")
    run("launchctl", "unload", "-w", str(plist_path(name)))


def bootstrap(name: str) -> tuple[bool, str]:
    code, out = run("launchctl", "bootstrap", DOMAIN, str(plist_path(name)))
    if code != 0:
        # 老系统只认 load
        code2, out2 = run("launchctl", "load", "-w", str(plist_path(name)))
        return code2 == 0, out2 or out
    return True, out


def port_busy(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=1.5
        ) as r:
            return r.status < 500
    except urllib.error.HTTPError:
        return True          # 404 也算有人在听
    except Exception:
        return False


def free_port(port: int) -> None:
    """把非 launchd 托管的旧实例请走，否则新进程会 EADDRINUSE 反复重启。"""
    code, out = run("lsof", "-nP", "-iTCP:" + str(port), "-sTCP:LISTEN", "-t")
    for pid in out.split():
        if pid.isdigit():
            run("kill", pid)


def install() -> int:
    AGENTS.mkdir(parents=True, exist_ok=True)
    LOGDIR.mkdir(parents=True, exist_ok=True)
    bad = 0
    for name in SERVICES:
        port = SERVICES[name][2]
        if port_busy(port):
            free_port(port)
            import time
            time.sleep(0.6)
        bootout(name)
        path = plist_path(name)
        path.write_bytes(plistlib.dumps(build(name)))
        ok, out = bootstrap(name)
        print(f"{'✅' if ok else '❌'} {label_of(name):32s} :{port}"
              + ("" if ok else f"  {out[:160]}"))
        bad += 0 if ok else 1
    return 1 if bad else 0


def uninstall() -> int:
    for name in SERVICES:
        bootout(name)
        p = plist_path(name)
        if p.exists():
            p.unlink()
        print(f"· 已卸载 {label_of(name)}")
    return 0


def status() -> int:
    import json
    import time
    for name, (_, script, port, _) in SERVICES.items():
        code, out = run("launchctl", "print", f"{DOMAIN}/{label_of(name)}")
        if code != 0:
            print(f"  {port:<5} {name:<10} 未加载")
            continue
        pid = ""
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("pid = "):
                pid = line.split("=", 1)[1].strip()
                break
        health = ""
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=2
            ) as r:
                health = str(r.status)
        except urllib.error.HTTPError as exc:
            health = f"{exc.code}（无 /api/health）"
        except Exception:
            health = "无响应"
        print(f"  {port:<5} {name:<10} launchd pid={pid or '-':<7} health={health}")
    return 0


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "install":
        return install()
    if action == "uninstall":
        return uninstall()
    if action == "restart":
        name = sys.argv[2] if len(sys.argv) > 2 else "page"
        if name not in SERVICES:
            print("可用：", "、".join(SERVICES)); return 2
        bootout(name); ok, out = bootstrap(name)
        print(("✅ 已重启 " if ok else "❌ ") + label_of(name) + ("" if ok else f" {out[:160]}"))
        return 0 if ok else 1
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
