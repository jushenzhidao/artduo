#!/usr/bin/env bash
# 一键拉起本地开发环境（页面 + 测试实例 + 两个 mock）
#
#   bash tools/dev_up.sh          # 启动（已在跑的就跳过）
#   bash tools/dev_up.sh status    # 看状态
#   bash tools/dev_up.sh stop      # 全部停掉
#   bash tools/dev_up.sh hold      # 前台守着（给 agent 用：后台任务活着，子进程就活着）
#
# 你从终端跑，进程就跟着你的 shell 活；由 agent 的后台任务起的实例会在轮次结束时被回收。
#
# 想彻底不受终端影响 → 用 launchd 托管（同一套端口与命令）：
#   python3 tools/launchd.py install     # 页面 + 两个 mock 交给 launchd，崩了自动拉起
#   python3 tools/launchd.py status
#   python3 tools/launchd.py uninstall
# 注意：launchctl bootstrap 必须在真实终端里执行 —— agent 沙箱里会被拒（EIO）。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="${TMPDIR:-/tmp}/jimeng-dev"
mkdir -p "$LOG"
PY="${PYTHON:-python3}"

# 端口 → 用途
#  8788 页面主服务（真实 flux + DeepSeek + 云端拼接）
#  8799 mock flux 上游（零计费）   8795 mock avm 上游
#  8790 分镜/端到端 mock 实例      8793 配额 0（错误态）
#  8791 优化项/参考图回归          8796 视频 mock 实例
#  8797 无 AVM_COOKIE（未配置态）
#  8798 文本模型关闭实例
start_one() {
  local port="$1"; shift
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "· $port 已在跑，跳过"
    return
  fi
  # shellcheck disable=SC2086
  if [ "${HOLD:-0}" = "1" ]; then
    env "$@" "$PY" "$APP" $APP_ARGS >"$LOG/$port.log" 2>&1 &
  else
    env "$@" nohup "$PY" "$APP" $APP_ARGS >"$LOG/$port.log" 2>&1 &
  fi
  echo $! >"$LOG/$port.pid"
  echo "· $port 启动（pid $(cat "$LOG/$port.pid")，日志 $LOG/$port.log）"
}

status_one() {
  local port="$1" name="$2"
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    local code
    code=$(curl -s --noproxy '*' -m 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/api/health" 2>/dev/null)
    # mock 上游没有 /api/health，端口在听就是活的
    if [ "$code" = "404" ]; then
      printf '  %-5s %-28s 在跑（mock，无 /api/health）\n' "$port" "$name"
    else
      printf '  %-5s %-28s 在跑（health %s）\n' "$port" "$name" "${code:-n/a}"
    fi
  else
    printf '  %-5s %-28s 已停\n' "$port" "$name"
  fi
}

case "${1:-up}" in
  stop)
    for f in "$LOG"/*.pid; do
      [ -f "$f" ] || continue
      pid=$(cat "$f")
      kill "$pid" 2>/dev/null && echo "· 停 pid ${pid}（$(basename "$f" .pid)）"
      rm -f "$f"
    done
    # 兜底：按端口找
    for p in 8788 8790 8793 8796 8797 8798 8799 8795; do
      pid=$(lsof -nP -iTCP:"$p" -sTCP:LISTEN -t 2>/dev/null | head -1)
      [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "· 停 ${p}（pid ${pid}）"
    done
    exit 0
    ;;
  launchd)
    exec "$PY" tools/launchd.py install
    ;;
  hold)
    export HOLD=1
    ;;
  status)
    echo "本地环境："
    status_one 8788 "页面主服务"
    status_one 8799 "mock flux"
    status_one 8795 "mock avm"
    status_one 8790 "分镜/e2e mock"
    status_one 8791 "优化/参考图 mock"
    status_one 8793 "配额 0（错误态）"
    status_one 8796 "视频 mock"
    status_one 8797 "无 AVM_COOKIE"
    status_one 8798 "文本模型关闭"
    exit 0
    ;;
esac

echo "启动本地环境（日志目录 ${LOG}）"
APP=tools/mock_upstream.py APP_ARGS="" start_one 8799 MOCK_PORT=8799
APP=tools/mock_avm.py      APP_ARGS="" start_one 8795
APP=server.py APP_ARGS="--port 8788" start_one 8788 JIMENG_MAX_CALLS=50
# 8790 给分镜/端到端用：文本模型关、视频**故意不配**（否则 F10 会真提交视频任务）
APP=server.py APP_ARGS="--port 8790" start_one 8790 JIMENG_LLM=off BFL_BASE=http://127.0.0.1:8799 \
  BFL2OPENAI_API_KEY=mock-key JIMENG_CONFIG_FILE=/tmp/sb-runtime-8790.json JIMENG_MAX_CALLS=500 \
  AVM_COOKIE_FILE=/tmp/no-such-cookie.txt AVM_MINTER_URL=http://127.0.0.1:39999
APP=server.py APP_ARGS="--port 8791" start_one 8791 JIMENG_LLM=off BFL_BASE=http://127.0.0.1:8799 \
  BFL2OPENAI_API_KEY=mock-key JIMENG_CONFIG_FILE=/tmp/sb-runtime-8791.json JIMENG_MAX_CALLS=500
APP=server.py APP_ARGS="--port 8793" start_one 8793 JIMENG_LLM=off BFL_BASE=http://127.0.0.1:8799 \
  BFL2OPENAI_API_KEY=mock-key JIMENG_CONFIG_FILE=/tmp/sb-runtime-8793.json JIMENG_MAX_CALLS=0
APP=server.py APP_ARGS="--port 8796" start_one 8796 JIMENG_LLM=off AVM_BASE=http://127.0.0.1:8795 \
  AVM_COOKIE=mock-cookie AVM_MINTER_URL=http://127.0.0.1:39999 JIMENG_MAX_VIDEOS=20 \
  BFL_BASE=http://127.0.0.1:8799 BFL2OPENAI_API_KEY=mock-key \
  JIMENG_CONFIG_FILE=/tmp/vc-8796.json JIMENG_MAX_CALLS=50
APP=server.py APP_ARGS="--port 8797" start_one 8797 JIMENG_LLM=off AVM_COOKIE_FILE=/tmp/no-such-cookie.txt \
  AVM_MINTER_URL=http://127.0.0.1:39999 BFL_BASE=http://127.0.0.1:8799 \
  BFL2OPENAI_API_KEY=mock-key JIMENG_CONFIG_FILE=/tmp/vc-8797.json JIMENG_MAX_CALLS=50
APP=server.py APP_ARGS="--port 8798" start_one 8798 JIMENG_LLM=off BFL_BASE=http://127.0.0.1:8799 \
  BFL2OPENAI_API_KEY=mock-key JIMENG_CONFIG_FILE=/tmp/sb-runtime-8798.json JIMENG_MAX_CALLS=30

if [ "${HOLD:-0}" = "1" ]; then
  echo "前台守着（Ctrl-C 结束）：日志在 ${LOG}"
  wait
  exit 0
fi

sleep 2
bash "$0" status
echo
echo "页面： http://127.0.0.1:8788/?view=drama"
echo "停掉： bash tools/dev_up.sh stop"
echo "常驻： bash tools/dev_up.sh launchd（交给 launchd，崩了自动拉起）"
