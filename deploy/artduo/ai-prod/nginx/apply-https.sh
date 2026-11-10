#!/usr/bin/env bash
# artduo.1task.cn · ai-prod 域名入口一键落地（幂等；DNS 未就绪会拒绝执行）
#
# 用法（在 ai-prod 上跑，root）：
#   bash /opt/artduo/nginx/apply-https.sh
#
# 流程：DNS 预检 → ACME 通道自检 → acme.sh 签发（HTTP-01 webroot）→ install-cert
#      → 投 :443 vhost → nginx -t + reload → 验证矩阵 + 终态断言
set -euo pipefail

HOST="${HOST:-artduo.1task.cn}"
IP_EXPECT="${IP_EXPECT:-210.121.44.245}"
VHOST_DIR=/www/server/panel/vhost/nginx
CERT_DIR="/www/server/panel/vhost/cert/$HOST"
ACME=/root/.acme.sh/acme.sh
NGINX=/www/server/nginx/sbin/nginx
SRC_DIR=/opt/artduo/nginx
# Basic Auth 凭据（部署时生成）
BASIC_USER="${BASIC_USER:-artduo}"
BASIC_PASS="${BASIC_PASS:-}"

echo "== 1) DNS 预检：$HOST 必须已指向 $IP_EXPECT =="
GOT=""
for R in dns13.hichina.com dns14.hichina.com 8.8.8.8 1.1.1.1 223.5.5.5; do
  GOT=$(dig +short "$HOST" @"$R" 2>/dev/null | grep -E '^[0-9]+\.' | head -1 || true)
  [ "$GOT" = "$IP_EXPECT" ] && break
done
if [ "$GOT" != "$IP_EXPECT" ]; then
  echo "❌ DNS 未就绪：$HOST -> '${GOT:-<空>}'（期望 $IP_EXPECT）—— 先加 A 记录再跑本脚本"
  exit 2
fi
echo "✅ $HOST -> $GOT"

echo "== 2) ACME 通道自检 =="
mkdir -p /www/wwwroot/acme/.well-known/acme-challenge
echo "preflight-$$" > /www/wwwroot/acme/.well-known/acme-challenge/preflight
CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" http://127.0.0.1/.well-known/acme-challenge/preflight)
[ "$CODE" = "200" ] || { echo "❌ 挑战路径返回 $CODE（应 200）—— 检查 $VHOST_DIR/$HOST.conf"; exit 3; }
echo "✅ 挑战路径 200"

echo "== 3) 签发证书（已存在则跳过）=="
if [ -d "/root/.acme.sh/${HOST}_ecc" ]; then
  echo "· 证书已存在，跳过签发"
else
  "$ACME" --issue -d "$HOST" --webroot /www/wwwroot/acme --keylength ec-256
fi
mkdir -p "$CERT_DIR"
"$ACME" --install-cert -d "$HOST" --ecc \
  --key-file       "$CERT_DIR/privkey.pem" \
  --fullchain-file "$CERT_DIR/fullchain.pem" \
  --reloadcmd      "$NGINX -s reload"
echo "✅ 证书就位：$CERT_DIR"

echo "== 4) Basic Auth（已存在则复用 .basic-auth）=="
AUTH_FILE="$SRC_DIR/.basic-auth"
if [ -s "$AUTH_FILE" ]; then
  BASIC_USER=$(cut -d: -f1 "$AUTH_FILE")
  BASIC_PASS=$(cut -d: -f2- "$AUTH_FILE")
fi
if [ ! -s "$SRC_DIR/.htpasswd" ]; then
  if [ -z "$BASIC_PASS" ]; then
    BASIC_PASS=$(python3 -c "import secrets,string;print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(16)))")
  fi
  HASH=$(openssl passwd -apr1 "$BASIC_PASS")
  printf '%s:%s\n' "$BASIC_USER" "$HASH" > "$SRC_DIR/.htpasswd"
  printf '%s:%s\n' "$BASIC_USER" "$BASIC_PASS" > "$AUTH_FILE"
  # .htpasswd 必须让 nginx worker（www）读得到；明文密码只留 root
  chmod 644 "$SRC_DIR/.htpasswd"
  chmod 600 "$AUTH_FILE"
  echo "✅ 已生成 Basic Auth：$BASIC_USER / $BASIC_PASS"
else
  echo "· .htpasswd 已存在，复用 $AUTH_FILE"
fi
[ -n "${BASIC_PASS:-}" ] || { echo "❌ 无法恢复 Basic Auth 密码：在 $AUTH_FILE 写 user:pass 后重跑"; exit 5; }
# 幂等：已有 .htpasswd 也要把权限修到 www 可读
chmod 644 "$SRC_DIR/.htpasswd"
AUTH=(-u "$BASIC_USER:$BASIC_PASS")

echo "== 5) 投 :443 vhost 并 reload（幂等）=="
cp -f "$SRC_DIR/$HOST-ssl.conf" "$VHOST_DIR/$HOST-ssl.conf"
"$NGINX" -t
"$NGINX" -s reload
sleep 2

echo "== 6) 验证矩阵 =="
fail=0
say() { printf '  %-42s %s\n' "$1" "$2"; }

code80=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" http://127.0.0.1/)
[ "$code80" = "301" ] && say ":80 → 301" "✅" || { say ":80 → 301" "❌ $code80"; fail=1; }

unauth=$(curl -s --resolve "$HOST:443:127.0.0.1" -o /dev/null -w '%{http_code}' "https://$HOST/")
[ "$unauth" = "401" ] && say ":443 无密码 → 401" "✅" || { say ":443 无密码 → 401" "❌ $unauth"; fail=1; }

health=$(curl -s "${AUTH[@]}" --resolve "$HOST:443:127.0.0.1" -o /tmp/artduo-health.json -w '%{http_code}' "https://$HOST/api/health")
[ "$health" = "200" ] && say ":443 /api/health" "✅" || { say ":443 /api/health" "❌ $health"; fail=1; }
grep -q '"ok": true' /tmp/artduo-health.json && say "health 内容" "✅" || { say "health 内容" "❌"; fail=1; }

home=$(curl -s "${AUTH[@]}" --resolve "$HOST:443:127.0.0.1" -o /tmp/artduo-home.html -w '%{http_code}' "https://$HOST/")
[ "$home" = "200" ] && say ":443 首页" "✅" || { say ":443 首页" "❌ $home"; fail=1; }
grep -q '即梦' /tmp/artduo-home.html && say "首页内容" "✅" || { say "首页内容" "❌"; fail=1; }

trace=$(curl -s "${AUTH[@]}" --resolve "$HOST:443:127.0.0.1" -o /dev/null -w '%{http_code}' "https://$HOST/api/trace")
[ "$trace" = "404" ] && say ":443 /api/trace 已封" "✅" || { say ":443 /api/trace 已封" "❌ $trace"; fail=1; }

cert=$(curl -s "${AUTH[@]}" --resolve "$HOST:443:127.0.0.1" -o /dev/null -w '%{ssl_verify_result}' "https://$HOST/api/health")
[ "$cert" = "0" ] && say "证书链校验" "✅" || { say "证书链校验" "❌ verify=$cert"; fail=1; }

if [ "$fail" = "0" ]; then
  echo "✅ 入口验证全部通过"
else
  echo "❌ 入口验证有失败项"
  exit 4
fi
