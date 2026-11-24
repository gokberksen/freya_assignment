#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p .runtime
ollama_pid=""
cleanup() {
    if [ -n "$ollama_pid" ]; then kill "$ollama_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM
if ! curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_MODELS="${OLLAMA_MODELS:-$PWD/.local/ollama}" OLLAMA_HOST=127.0.0.1:11434 ollama serve >.runtime/ollama.log 2>&1 &
    ollama_pid=$!
fi
.venv/bin/python -m uvicorn freya.app:app --host 127.0.0.1 --port "${FREYA_PORT:-8765}"
