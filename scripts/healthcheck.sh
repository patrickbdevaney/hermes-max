#!/usr/bin/env bash
# Ping every component INDEPENDENTLY. Same on laptop and your inference host.
# Exit non-zero if any of the five MCP servers is down (supporting services are
# reported but do not fail the check — the agent degrades gracefully without them).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

echo "═══ hermes-max healthcheck ═══"
echo "DEPLOY_PROFILE=${HMX_PROFILE}  (active servers: ${HMX_ACTIVE_SERVERS[*]})"
DOWN=0
for name in "${HMX_ACTIVE_SERVERS[@]}"; do
  url="$(hmx_health_url "$name")"
  if body="$(curl -fsS -m 5 "${url}" 2>/dev/null)"; then
    echo "  ✓ ${HMX_DIR[$name]}  ($(hmx_port "$name"))  ${body}"
  else
    echo "  ✗ ${HMX_DIR[$name]}  ($(hmx_port "$name"))  DOWN"
    DOWN=1
  fi
done

echo "── supporting services (informational) ──"
# Stage-1 local model servers (optional; RAG degrades to BM25+graph without them).
if [ -n "${EMBED_BASE_URL:-}" ]; then
  curl -fsS -m 3 "${EMBED_BASE_URL%/}/embeddings" -H 'Content-Type: application/json' \
    -d '{"input":["ping"]}' >/dev/null 2>&1 \
    && echo "  ✓ embeddings (${EMBED_BASE_URL}) — RAG dense lane ON" \
    || echo "  • embeddings (${EMBED_BASE_URL}) down — RAG falls back to BM25+graph (run ./serve-embed.sh)"
else
  echo "  • EMBED_BASE_URL unset — RAG dense lane off (BM25+graph)"
fi
if [ -n "${RERANK_BASE_URL:-}" ]; then
  curl -fsS -m 3 "${RERANK_BASE_URL%/}/rerank" -H 'Content-Type: application/json' \
    -d '{"query":"ping","documents":["a"]}' >/dev/null 2>&1 \
    && echo "  ✓ reranker (${RERANK_BASE_URL}) — RAG +rerank ON" \
    || echo "  • reranker (${RERANK_BASE_URL}) down — RAG returns fused order (run ./serve-rerank.sh)"
else
  echo "  • RERANK_BASE_URL unset — RAG rerank lane off"
fi
hmx_phoenix_otlp_ok \
  && echo "  ✓ Phoenix OTLP (4317)" || echo "  • Phoenix OTLP down (run ./phoenix.sh)"
curl -fsS -m 2 "http://localhost:6006" >/dev/null 2>&1 \
  && echo "  ✓ Phoenix UI (6006)" || echo "  • Phoenix UI down"
curl -fsS -m 2 "http://localhost:8080" >/dev/null 2>&1 \
  && echo "  ✓ SearXNG (8080)" || echo "  • SearXNG down (run ./searXNG.sh)"
if [ -n "${VLLM_BASE_URL:-}" ]; then
  if models_json="$(curl -fsS -m 5 "${VLLM_BASE_URL}/models" 2>/dev/null)"; then
    echo "  ✓ vLLM (${VLLM_BASE_URL})"
    # Long-horizon skills assume the full ~262K window. Warn (don't fail) if the
    # served context is small — that is the "Hermes lost the plan at 65K" trap.
    mml="$(printf '%s' "${models_json}" | python3 -c \
      'import json,sys; d=json.load(sys.stdin).get("data") or [{}]; print(d[0].get("max_model_len") or 0)' \
      2>/dev/null || echo 0)"
    if [ "${mml}" -ge 200000 ] 2>/dev/null; then
      echo "  ✓ vLLM max_model_len=${mml} (long-horizon ready)"
    else
      echo "  ⚠ vLLM max_model_len=${mml} < 200000 — long-horizon skills assume ~262K."
      echo "    Re-serve in longctx mode (MAX_LEN=262144); see README 'Long-horizon prerequisite'."
    fi
  else
    echo "  • vLLM NOT reachable (${VLLM_BASE_URL})"
  fi
else
  echo "  • VLLM_BASE_URL unset"
fi

echo
[ "${DOWN}" -eq 0 ] && { echo "all MCP servers healthy"; exit 0; } || { echo "one or more MCP servers DOWN"; exit 1; }
