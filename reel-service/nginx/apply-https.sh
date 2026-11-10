#!/usr/bin/env bash
# reel-service · ai-prod 域名入口一键落地（幂等；DNS 未就绪会拒绝执行）
#
# 用法（在 ai-prod 上跑，root）：
#   bash /opt/reel/nginx/apply-https.sh
#
# 流程：DNS 预检 → ACME 通道自检 → acme.sh 签发（HTTP-01 webroot）→ install-cert
#      （含续期 reloadcmd）→ 投 :443 vhost → nginx -t + reload → 验证矩阵 + 终态断言
set -euo pipefail

HOST="${HOST:-reel.1task.cn}"
IP_EXPECT="${IP_EXPECT:-210.121.44.245}"
VHOST_DIR=/www/server/panel/vhost/nginx
CERT_DIR="/www/server/panel/vhost/cert/$HOST"
ACME=/root/.acme.sh/acme.sh
NGINX=/www/server/nginx/sbin/nginx
SRC_DIR=/opt/reel/nginx

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

echo "== 4) 投 :443 vhost 并 reload（幂等）=="
cp -f "$SRC_DIR/$HOST-ssl.conf" "$VHOST_DIR/$HOST-ssl.conf"
"$NGINX" -t
"$NGINX" -s reload
sleep 2

echo "== 5) 验证矩阵 =="
fail=0
say() { printf '  %-42s %s\n' "$1" "$2"; }
code80=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" http://127.0.0.1/)
[ "$code80" = "301" ] && say ":80 → 301" "✅" || { say ":80 → 301" "❌ $code80"; fail=1; }
# :443 验证必须带 SNI（--resolve），只加 Host 头会落到 default_server
health=$(curl -s --resolve "$HOST:443:127.0.0.1" -o /tmp/reel-health.json -w '%{http_code}' "https://$HOST/v1/healthz")
[ "$health" = "200" ] && say ":443 /v1/healthz" "✅" || { say ":443 /v1/healthz" "❌ $health"; fail=1; }
grep -q 'reel-service' /tmp/reel-health.json && say "health 内容" "✅" || { say "health 内容" "❌"; fail=1; }
unauth=$(curl -s --resolve "$HOST:443:127.0.0.1" -o /dev/null -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' -d '{"segments":[]}' "https://$HOST/v1/reel")
[ "$unauth" = "401" ] && say ":443 无 key 提交 → 401" "✅" || { say ":443 无 key 提交 → 401" "❌ $unauth"; fail=1; }
cert=$(curl -s --resolve "$HOST:443:127.0.0.1" -o /dev/null -w '%{ssl_verify_result}' "https://$HOST/v1/healthz")
[ "$cert" = "0" ] && say "证书链校验" "✅" || { say "证书链校验" "❌ verify=$cert"; fail=1; }
other=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: not-registered.1task.cn" http://127.0.0.1/)
say "未登记 Host（不走本 vhost）" "· $other"

if [ "$fail" = "0" ]; then
  echo "✅ 入口验证全部通过"
else
  echo "❌ 入口验证有失败项"
  exit 4
fi
