#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Crawl4AI — the SOVEREIGN web-extract backend (replaces Firecrawl/Tavily/Exa).
# Self-hosted, no API key, Playwright-backed; emits clean RAG-optimised markdown.
# mcp-docs fetch_clean/ingest_doc call it; with it up, the whole docs loop
# (SearXNG → Crawl4AI → local distil → RAG/KG) needs NO external API.
#
# Same fire-and-forget pattern as phoenix.sh / searXNG.sh. Binds localhost:11235.
#
# Arch-aware (the your inference host is ARM64, laptops are x86_64):
#   • aarch64/arm64 → --platform linux/arm64, tag ':basic' (markdown extraction
#     only — the ':all' tag pulls torch+transformers, redundant on your inference host where
#     the chat model already owns the GPU).
#   • x86_64        → --platform linux/amd64, tag ':basic'.
# After first launch, crawl4ai-setup runs INSIDE the container to install the
# Playwright browsers.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

PORT="${CRAWL4AI_PORT:-11235}"
NAME="${CRAWL4AI_NAME:-crawl4ai}"

# Tag choice is arch-dependent: the ':basic' tag (markdown-only, no torch — ideal
# on your inference host where the chat model already owns the GPU) is published ONLY for
# arm64. amd64 has no ':basic', so x86_64 uses the multi-arch ':latest'. Override
# either with CRAWL4AI_TAG.
ARCH_RAW="$(uname -m)"
case "${ARCH_RAW}" in
  aarch64|arm64) PLATFORM="linux/arm64"; TAG="${CRAWL4AI_TAG:-basic}" ;;
  x86_64|amd64)  PLATFORM="linux/amd64"; TAG="${CRAWL4AI_TAG:-latest}" ;;
  *)             PLATFORM="linux/amd64"; TAG="${CRAWL4AI_TAG:-latest}"
                 echo "  (unknown arch ${ARCH_RAW}; defaulting to linux/amd64:${TAG})" ;;
esac
IMAGE="unclecode/crawl4ai:${TAG}"

echo "→ Crawl4AI: arch=${ARCH_RAW} platform=${PLATFORM} image=${IMAGE} port=${PORT}"

if docker ps --format '{{.Names}}' | grep -qx "${NAME}"; then
  echo "  ✓ already running"
elif docker ps -a --format '{{.Names}}' | grep -qx "${NAME}"; then
  echo "  → starting existing container"
  docker start "${NAME}" >/dev/null
else
  echo "  → pulling + running ${IMAGE}"
  docker run -d --name "${NAME}" \
    --platform "${PLATFORM}" \
    -p "${PORT}:11235" \
    --shm-size=1g \
    --restart unless-stopped \
    "${IMAGE}"
  # Install Playwright browsers inside the container (idempotent; harmless if the
  # image already bundles them).
  echo "  → running crawl4ai-setup inside the container (Playwright browsers)"
  docker exec "${NAME}" crawl4ai-setup >/dev/null 2>&1 \
    && echo "    ✓ crawl4ai-setup done" \
    || echo "    • crawl4ai-setup skipped/!=0 (image may already bundle browsers)"
fi

echo
echo "  health:   curl -s http://localhost:${PORT}/health"
echo "  set in .env:  CRAWL4AI_URL=http://localhost:${PORT}"
echo "  stop:     docker stop ${NAME}   |   logs: docker logs ${NAME}"
