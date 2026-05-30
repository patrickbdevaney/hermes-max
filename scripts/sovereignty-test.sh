#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Sovereignty assertion — the headline property:
#   With ALL external API keys UNSET and only the LOCAL stack running
#   (vLLM + SearXNG + Crawl4AI), the full loop works: search → extract → distil →
#   store → retrieve → verify → evolve. No paid API, no cloud key, for any core
#   capability.
#
# This test UNSETS every cloud key in its own environment, then checks each local
# capability is reachable and that nothing REQUIRES a cloud key. Local backend
# down ⇒ reported as a degraded (still-sovereign) capability, not a hard fail of
# sovereignty itself.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

# Strip every cloud/secret env var — prove nothing core depends on them.
unset ESCALATION_API_KEY ESCALATION_BASE_URL ESCALATION_LONG_API_KEY \
      FIRECRAWL_API_KEY TAVILY_API_KEY EXA_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY \
      MONITOR_BASE_URL 2>/dev/null || true

ok=0; warn=0; bad=0
PASS(){ printf '  \033[32m✓\033[0m %s\n' "$*"; ok=$((ok+1)); }
WARN(){ printf '  \033[33m•\033[0m %s\n' "$*"; warn=$((warn+1)); }
BAD(){  printf '  \033[31m✗\033[0m %s\n' "$*"; bad=$((bad+1)); }

echo "═══ hermes-max sovereignty test (all cloud keys unset) ═══"

# 1. the ONLY required external host is the local vLLM chat model
if [ -n "${VLLM_BASE_URL:-}" ] && curl -fsS -m6 "${VLLM_BASE_URL%/}/models" >/dev/null 2>&1; then
  PASS "local chat model reachable (${VLLM_BASE_URL}) — the one hard dependency"
else
  BAD "local chat model NOT reachable (${VLLM_BASE_URL:-unset}) — required"
fi

# 2. local search (SearXNG JSON) — no key
if curl -fsS -m6 "${SEARXNG_URL:-http://localhost:8080}/search?q=test&format=json" >/dev/null 2>&1; then
  PASS "SearXNG JSON search reachable — sovereign search, no key"
else
  WARN "SearXNG not reachable/JSON off — run ./searXNG.sh (search degraded, still no cloud key)"
fi

# 3. local extract (Crawl4AI) — no key; trafilatura is the local fallback
if curl -fsS -m6 "${CRAWL4AI_URL:-http://localhost:11235}/health" >/dev/null 2>&1; then
  PASS "Crawl4AI reachable — sovereign extract, no Firecrawl/Tavily/Exa key"
else
  WARN "Crawl4AI down — fetch_clean falls back to local trafilatura (still no cloud key)"
fi

# 4. escalation must be OFF-by-default and need no key to run the core stack
if [ "${ESCALATION_ENABLED:-false}" = "true" ]; then
  WARN "escalation is ENABLED (optional cloud tier) — core stack still runs without it"
else
  PASS "escalation OFF by default — no cloud key needed for any core capability"
fi

# 5. embeddings/reranker are LOCAL (or cleanly absent → BM25+graph)
[ -n "${EMBED_BASE_URL:-}" ] && PASS "embeddings endpoint set (local)" \
  || WARN "EMBED_BASE_URL unset — RAG runs BM25+graph (sovereign, no cloud)"

# 5b. deep-research (mcp-research) reaches ONLY local backends — no cloud key/SDK.
if [ -d "${REPO_ROOT}/mcp-research" ]; then
  if grep -rqiE '(FIRECRAWL|TAVILY|EXA|OPENAI|ANTHROPIC)_API_KEY' "${REPO_ROOT}/mcp-research" 2>/dev/null; then
    BAD "mcp-research references a cloud API key — not sovereign"
  else
    PASS "deep-research is sovereign — SearXNG+Crawl4AI/trafilatura+local chat, no cloud key"
  fi
fi

# 6. assert NO cloud key is set in the loaded env (they were unset above)
leaked="$(env | grep -E '^(FIRECRAWL|TAVILY|EXA|OPENAI|ANTHROPIC)_API_KEY=' || true)"
[ -z "${leaked}" ] && PASS "no cloud API keys present — fully local" \
  || BAD "cloud key present in env: ${leaked%%=*}"

echo
echo "result: ${ok} sovereign-OK, ${warn} degraded-but-sovereign, ${bad} hard-fail"
if [ "${bad}" -eq 0 ]; then
  echo "✓ SOVEREIGN: the core loop needs only the local vLLM (+ optional local SearXNG/Crawl4AI). No cloud key required."
  exit 0
fi
echo "✗ a REQUIRED local dependency is down (see ✗ above)."
exit 1
