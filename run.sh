#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_root"

runtime_dir="$project_root/.local/run"
log_dir="$project_root/.local/logs"
mkdir -p "$runtime_dir" "$log_dir"
chmod 700 "$runtime_dir" "$log_dir"

declare -a service_pids=()

cleanup() {
  trap - INT TERM EXIT
  for pid in "${service_pids[@]}"; do
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  done
  for pid in "${service_pids[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
  echo "AgentFlow stopped. PostgreSQL stays running; start it again with ./run.sh."
}
trap cleanup INT TERM EXIT

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js and npm are required." >&2
  exit 1
fi
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import agentflow, alembic, fastapi, psycopg, redis, uvicorn' >/dev/null 2>&1; then
  echo "Installing Python dependencies..."
  .venv/bin/python -m pip install -r requirements.lock
  .venv/bin/python -m pip install --no-build-isolation --no-deps -e .
fi
if [[ ! -x apps/web/node_modules/.bin/next ]]; then
  echo "Installing web dependencies..."
  (cd apps/web && npm ci)
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
fi
if [[ ! -f apps/web/.env.local ]]; then
  cp apps/web/.env.example apps/web/.env.local
fi
redis_url="$(.venv/bin/python -c 'import os; from dotenv import dotenv_values; print(os.environ.get("REDIS_URL") or dotenv_values(".env").get("REDIS_URL") or "")')"
redis_url="${redis_url:-redis://127.0.0.1:6379/0}"
export REDIS_URL="$redis_url"

database_host="$(.venv/bin/python -c 'from sqlalchemy.engine import make_url; from agentflow.config import get_settings; print(make_url(get_settings().database_url).host or "")')"
case "$database_host" in
  127.0.0.1|localhost|::1)
    if ! command -v pg_config >/dev/null 2>&1; then
      echo "Local PostgreSQL server tools are required for a local database (pg_config, initdb, and pg_ctl)." >&2
      exit 1
    fi
    echo "Starting project-local PostgreSQL..."
    scripts/start-local-postgres.sh
    ;;
  *)
    echo "Using configured PostgreSQL host: $database_host"
    ;;
esac
echo "Applying database migrations..."
.venv/bin/python -m alembic upgrade head

freellmapi_dir="${FREELLMAPI_DIR:-$HOME/freellmapi}"
if grep -Eq '^FREELLMAPI_API_KEY=.{1,}' .env && [[ -f "$freellmapi_dir/docker-compose.yml" ]]; then
  echo "Starting FreeLLMAPI for AI workflow proposals..."
  if ! FREELLMAPI_DIR="$freellmapi_dir" scripts/configure_freellmapi_dns.sh; then
    echo "Warning: FreeLLMAPI could not be started; the rest of AgentFlow will still run." >&2
  fi
else
  echo "FreeLLMAPI auto-start skipped; configure its compose project and API key to enable AI proposals."
fi

start_service() {
  local name="$1"
  shift
  local logfile="$log_dir/$name.log"
  setsid "$@" >"$logfile" 2>&1 &
  local pid=$!
  service_pids+=("$pid")
  echo "Started $name (log: .local/logs/$name.log)"
}

port_is_busy() {
  local port="$1"
  command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

redis_host="$(.venv/bin/python -c 'from urllib.parse import urlsplit; import os; print(urlsplit(os.environ["REDIS_URL"]).hostname or "")')"
redis_port="$(.venv/bin/python -c 'from urllib.parse import urlsplit; import os; print(urlsplit(os.environ["REDIS_URL"]).port or 6379)')"
case "$redis_host" in
  127.0.0.1|localhost|::1)
    if ! .venv/bin/python -c 'from redis import Redis; import os; Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=0.2, socket_timeout=0.2).ping()' >/dev/null 2>&1; then
      if command -v redis-server >/dev/null 2>&1 && ! port_is_busy "$redis_port"; then
        echo "Starting project-local Redis cache..."
        start_service redis redis-server --bind "$redis_host" --port "$redis_port" --save "" --appendonly no --dir "$runtime_dir"
        for _ in {1..20}; do
          if .venv/bin/python -c 'from redis import Redis; import os; Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=0.2, socket_timeout=0.2).ping()' >/dev/null 2>&1; then
            break
          fi
          sleep 0.25
        done
      else
        echo "Warning: Redis is unavailable; reference-data caching will run without Redis." >&2
      fi
    fi
    ;;
  *)
    if ! .venv/bin/python -c 'from redis import Redis; import os; Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=0.5, socket_timeout=0.5).ping()' >/dev/null 2>&1; then
      echo "Warning: configured Redis is unavailable; reference-data caching will run without Redis." >&2
    fi
    ;;
esac

if [[ "${ENABLE_LOCAL_OLLAMA:-0}" == "1" ]]; then
  local_ollama_bin="$project_root/.local/ollama/bin/ollama"
  ollama_bin="$(command -v ollama 2>/dev/null || true)"
  if [[ -x "$local_ollama_bin" ]]; then
    ollama_bin="$local_ollama_bin"
    export OLLAMA_MODELS="${OLLAMA_MODELS:-$project_root/.local/ollama-models}"
  fi
  if [[ -z "$ollama_bin" ]]; then
    echo "ENABLE_LOCAL_OLLAMA=1 but Ollama was not found in PATH or .local/ollama/bin." >&2
    exit 1
  fi
  if ! curl --silent --fail --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "Starting local Ollama service..."
    start_service ollama "$ollama_bin" serve
    for _ in {1..40}; do
      if curl --silent --fail --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        break
      fi
      sleep 0.25
    done
  fi
  if curl --silent --fail --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "Ollama is available at http://127.0.0.1:11434."
  else
    echo "Warning: Ollama did not become available; local Qwen proposals will fail." >&2
  fi
else
  echo "Local Ollama startup is disabled; FreeLLMAPI is the active AI provider."
fi

if curl --silent --show-error --fail --max-time 2 http://127.0.0.1:8001/health >/dev/null 2>&1; then
  echo "API is already responding at http://127.0.0.1:8001."
elif port_is_busy 8001; then
  echo "Port 8001 is occupied by another service; inspect it before starting AgentFlow." >&2
  exit 1
else
  start_service api .venv/bin/python -m agentflow
fi

start_service worker .venv/bin/python -m agentflow.worker

if curl --silent --fail --max-time 2 http://127.0.0.1:3000 >/dev/null 2>&1; then
  echo "Web app is already responding at http://127.0.0.1:3000."
elif port_is_busy 3000; then
  echo "Port 3000 is occupied by another service; inspect it before starting AgentFlow." >&2
  exit 1
else
  neon_function_health_url="$(.venv/bin/python -c 'from dotenv import dotenv_values; print(dotenv_values(".env").get("NEON_FUNCTION_HEALTH_BASE_URL", "") or "")')"
  if [[ -n "$neon_function_health_url" ]]; then
    export NEON_FUNCTION_HEALTH_BASE_URL="$neon_function_health_url"
  fi
  start_service web env -u REDIS_URL bash -c 'cd "$1" && exec ./node_modules/.bin/next dev --hostname 127.0.0.1' _ "$project_root/apps/web"
fi

echo
echo "AgentFlow is starting:"
echo "  Website: http://localhost:3000"
echo "  API:     http://localhost:8001"
echo "  API docs: http://localhost:8001/docs"
echo "Press Ctrl+C to stop services launched by this script. PostgreSQL stays running."
echo

while true; do
  sleep 1
  for pid in "${service_pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      set +e
      wait "$pid"
      status=$?
      set -e
      echo "A service exited (status $status). Check .local/logs/." >&2
      exit "$status"
    fi
  done
done
