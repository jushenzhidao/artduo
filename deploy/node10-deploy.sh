#!/usr/bin/env bash
# artduo image deploy — runs as the unprivileged `artduo` user (docker group).
set -euo pipefail

VERSION="${1:?usage: deploy.sh <version>}"
BASE="/opt/artduo"
cd "$BASE"

printf 'IMAGE_TAG=%s\n' "$VERSION" > .env
docker compose pull --quiet
docker compose up -d --remove-orphans

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:39120/ >/dev/null 2>&1; then
    printf '%s %s\n' "$VERSION" "$(date -u +%FT%TZ)" > .deployed
    docker image prune -f --filter until=168h >/dev/null 2>&1 || true
    echo "deployed $VERSION"
    exit 0
  fi
  sleep 1
done

echo "health check failed for $VERSION" >&2
docker logs --tail 80 artduo >&2 || true
exit 1
