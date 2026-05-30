#!/usr/bin/env bash
# Optionally PRE-SEED the docs/ RAG namespace + KG with an operator's own stack
# (framework docs, internal wikis exported to markdown, RFCs) so a fresh
# deployment already knows the domain. NOT required — the agent self-seeds on
# demand via workflow-learn-framework / research_topic; this is just a cheap
# accelerator, made into one idempotent command.
#
# Usage:
#   bash scripts/seed-docs.sh <topic> <url> [url ...]
#   bash scripts/seed-docs.sh --file urls.tsv        # lines: "<topic>\t<url>"
#
# Idempotent: re-ingesting the same (topic,url) replaces its prior chunks.
# Reaches mcp-docs over MCP; requires mcp-docs (9109) + RAG (9102) running.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

DOCS_PORT="${MCP_DOCS_PORT:-9109}"
DOCS_URL="http://$(hmx_bind_host):${DOCS_PORT}/mcp"
PY="${REPO_ROOT}/mcp-docs/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

declare -a PAIRS=()   # "topic<TAB>url"
if [ "${1:-}" = "--file" ]; then
  [ -f "${2:-}" ] || { echo "seed-docs: file not found: ${2:-}" >&2; exit 2; }
  while IFS=$'\t' read -r topic url; do
    [ -n "${topic}" ] && [ -n "${url}" ] && PAIRS+=("${topic}	${url}")
  done < "$2"
elif [ "$#" -ge 2 ]; then
  topic="$1"; shift
  for url in "$@"; do PAIRS+=("${topic}	${url}"); done
else
  echo "usage: seed-docs.sh <topic> <url> [url ...]   |   seed-docs.sh --file urls.tsv" >&2
  exit 2
fi

echo "═══ seeding ${#PAIRS[@]} doc(s) via mcp-docs (${DOCS_URL}) ═══"
printf '%s\n' "${PAIRS[@]}" | DOCS_MCP_URL="${DOCS_URL}" "${PY}" - <<'PYEOF'
import asyncio, json, os, sys

pairs = [ln.rstrip("\n").split("\t", 1) for ln in sys.stdin if "\t" in ln]
url = os.environ["DOCS_MCP_URL"]

async def ingest(topic, doc_url):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            out = await s.call_tool("ingest_doc", {"url_or_markdown": doc_url, "topic": topic})
            data = out.structuredContent or (json.loads(out.content[0].text) if out.content else {})
            return data.get("result", data)

async def main():
    ok = 0
    for topic, doc_url in pairs:
        try:
            res = await ingest(topic, doc_url)
            stored = res.get("rag_stored")
            print(f"  [{'✓' if stored else '•'}] {topic:<18} {doc_url[:55]}  "
                  f"(chunks_stored={stored}, apis={len(res.get('apis',[]))})")
            ok += 1 if stored else 0
        except Exception as e:  # noqa: BLE001
            print(f"  [✗] {topic:<18} {doc_url[:55]}  ERROR {type(e).__name__}: {e}")
    print(f"═══ seeded {ok}/{len(pairs)} ═══")

asyncio.run(main())
PYEOF
