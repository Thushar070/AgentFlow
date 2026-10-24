#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ollama_root="$project_root/.local/ollama"
models_dir="$project_root/.local/ollama-models"
runtime_dir="$project_root/.local/run"
log_dir="$project_root/.local/logs"
ollama_bin="$ollama_root/bin/ollama"
archive=""
started_here=0
ollama_pid=""
mkdir -p "$ollama_root" "$models_dir" "$runtime_dir" "$log_dir"
chmod 700 "$runtime_dir" "$log_dir"

cleanup() {
  if [[ "$started_here" == 1 ]]; then
    kill -- "-$ollama_pid" 2>/dev/null || kill "$ollama_pid" 2>/dev/null || true
    wait "$ollama_pid" 2>/dev/null || true
  fi
  if [[ -n "$archive" ]]; then
    rm -f "$archive"
  fi
}
trap cleanup EXIT

if [[ ! -x "$ollama_bin" ]]; then
  for tool in curl zstd tar; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "Required tool is missing: $tool" >&2
      exit 1
    fi
  done
  archive="$(mktemp /tmp/agentflow-ollama.XXXXXX.tar.zst)"
  echo "Downloading Ollama into the ignored .local directory..."
  curl -fL --retry 2 --connect-timeout 15 \
    https://ollama.com/download/ollama-linux-amd64.tar.zst -o "$archive"
  zstd -dc "$archive" | tar -xf - -C "$ollama_root"
fi

export OLLAMA_MODELS="$models_dir"
if ! curl --silent --fail --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "Starting Ollama temporarily to download the selected models..."
  setsid "$ollama_bin" serve >"$log_dir/ollama-setup.log" 2>&1 &
  ollama_pid=$!
  started_here=1
  for _ in {1..60}; do
    if curl --silent --fail --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      break
    fi
    sleep 0.25
  done
  if ! curl --silent --fail --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "Ollama did not become ready; see .local/logs/ollama-setup.log." >&2
    exit 1
  fi
fi

"$ollama_bin" pull qwen3:0.6b
"$ollama_bin" pull qwen3.5:0.8b
echo "Qwen models are installed under .local/ollama-models. ./run.sh will start Ollama."
