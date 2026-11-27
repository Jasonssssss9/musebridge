#!/usr/bin/env bash
# Run the gallery locally with the real retrieval, Go API and Ollama workflow.
set -euo pipefail
cd "$(dirname "$0")/.."
root="$PWD"
python_bin="${AGENT_PYTHON:-python3}"
: "${AGENT_TOKENIZER_PATH:?Set AGENT_TOKENIZER_PATH to the pinned Qwen tokenizer JSON}"
export DATASET_VERSION="${DATASET_VERSION:-getty-pilot-v1}"
export SEED_PATH="${SEED_PATH:-$root/knowledge/data/$DATASET_VERSION.json}"
export MUSEBRIDGE_RETRIEVAL_MODE="${MUSEBRIDGE_RETRIEVAL_MODE:-hybrid}"
retrieval_port="${RETRIEVAL_PORT:-8001}"
agent_port="${AGENT_PORT:-8002}"
web_port="${WEB_PORT:-8080}"
export RETRIEVAL_BASE_URL="http://127.0.0.1:$retrieval_port"
export AGENT_BASE_URL="http://127.0.0.1:$agent_port"
export HOST=127.0.0.1 PORT="${API_PORT:-8000}"
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://localhost:$web_port}"
export GENERATION_MODE=agent PUBLIC_CONTRACT=agent
export AGENT_INTEGRATION_MODE=controlled-ollama
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
export AGENT_SERVICE_TOKEN
AGENT_SERVICE_TOKEN="$("$python_bin" -c 'import secrets; print(secrets.token_urlsafe(32))')"
if [[ "$MUSEBRIDGE_RETRIEVAL_MODE" == hybrid && -z "${OPENAI_API_KEY:-}" ]]; then
    : "${OPENAI_API_KEY_FILE:?Set OPENAI_API_KEY or OPENAI_API_KEY_FILE for hybrid retrieval}"
    export OPENAI_API_KEY
    OPENAI_API_KEY="$(<"$OPENAI_API_KEY_FILE")"
fi
run_dir="$(mktemp -d -t musebridge-gallery.XXXXXXXX)"
children=()
cleanup() {
    if (( ${#children[@]} )); then
        kill "${children[@]}" 2>/dev/null || true
        wait "${children[@]}" 2>/dev/null || true
    fi
    rm -f "$run_dir/api"
    rmdir "$run_dir"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
go -C api build -o "$run_dir/api" ./cmd/server
"${RETRIEVAL_PYTHON:-$python_bin}" -m knowledge.http_service --host 127.0.0.1 --port "$retrieval_port" &
children+=("$!")
unset OPENAI_API_KEY OPENAI_API_KEY_FILE
"$python_bin" -m uvicorn agent.testing.integration_app:create_app --factory --host 127.0.0.1 --port "$agent_port" --no-access-log &
children+=("$!")
"$run_dir/api" &
children+=("$!")
echo "Gallery: $PUBLIC_BASE_URL/    QR cards: $PUBLIC_BASE_URL/qr"
echo "Real answer readiness: $AGENT_BASE_URL/readyz"
(
    cd web
    unset AGENT_SERVICE_TOKEN
    export API_TARGET="http://127.0.0.1:$PORT"
    exec node node_modules/vite/bin/vite.js --host "${WEB_HOST:-127.0.0.1}" --port "$web_port" --strictPort
) &
children+=("$!")
wait -n
