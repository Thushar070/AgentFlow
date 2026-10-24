#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
freellmapi_dir="${FREELLMAPI_DIR:-/home/billy/freellmapi}"
compose_file="$freellmapi_dir/docker-compose.yml"
override_file="$project_root/.local/freellmapi-dns.override.yml"

if [[ ! -f "$compose_file" ]]; then
  echo "FreeLLMAPI Compose file not found: $compose_file" >&2
  exit 1
fi

resolver="$(awk '$1 == "nameserver" && $2 ~ /^[0-9]+\./ { print $2; exit }' /etc/resolv.conf)"
if [[ -z "$resolver" ]]; then
  echo "No active IPv4 DNS resolver found in /etc/resolv.conf." >&2
  exit 1
fi

mkdir -p "$(dirname "$override_file")"
cat > "$override_file" <<EOF
services:
  freellmapi:
    dns:
      - "$resolver"
EOF
chmod 600 "$override_file"

docker compose \
  --project-directory "$freellmapi_dir" \
  -f "$compose_file" \
  -f "$override_file" \
  up -d --no-deps freellmapi
echo "FreeLLMAPI restarted with the current host DNS resolver ($resolver)."
