#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Honest quality eval for mcp-research's deep_research loop.
#
# Runs a SMALL FIXED set of factual questions end-to-end through the LIVE
# mcp-research server (over MCP), scoring:
#   • citation-correctness  — every answer is backed by >=1 source URL
#   • answer-correctness    — a known ground-truth substring appears in the report
#
# This is a cheap PROXY, reported honestly. The open-local bar is ~72-78% on
# simple factual questions fully local (stronger with a cloud chat endpoint). If
# retrieval is fine but synthesis is weak, the report says so — that informs
# whether to route synthesis to the escalation tier later (a separate spec).
#
# Requires the live sovereign loop: mcp-research (9110) + mcp-docs/SearXNG +
# $VLLM_BASE_URL. If any is down, the eval SKIPS cleanly (informational, exit 0)
# rather than reporting a meaningless 0%.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"
hmx_load_env

HOST="$(hmx_bind_host)"
PORT="${MCP_RESEARCH_PORT:-9110}"
URL="http://${HOST}:${PORT}/mcp"
PY="${REPO_ROOT}/mcp-research/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

echo "═══ mcp-research eval (live, sovereign) ═══"
# Preflight: research server, SearXNG, chat model.
if ! curl -fsS -m5 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "• mcp-research (${PORT}) not up — start: scripts/start-all.sh. SKIPPING (informational)."; exit 0
fi
if ! curl -fsS -m6 "${SEARXNG_URL:-http://localhost:8080}/search?q=test&format=json" >/dev/null 2>&1; then
  echo "• SearXNG not reachable — run ./searXNG.sh. SKIPPING live eval (informational)."; exit 0
fi
if [ -z "${VLLM_BASE_URL:-}" ] || ! curl -fsS -m6 "${VLLM_BASE_URL%/}/models" >/dev/null 2>&1; then
  echo "• chat model (${VLLM_BASE_URL:-unset}) not reachable. SKIPPING live eval (informational)."; exit 0
fi

MCP_URL="${URL}" "${PY}" - <<'PY'
import asyncio, json, os
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = os.environ["MCP_URL"]
# (question, list-of-acceptable ground-truth substrings)
CASES = [
    ("What HTTP status code means 'Too Many Requests'?", ["429"]),
    ("What is the default TCP port for PostgreSQL?", ["5432"]),
    ("Which shell command initializes a new empty git repository?", ["git init"]),
    ("What does the HTTP 404 status code mean?", ["not found"]),
    ("Which Python syntax defines an asynchronous function?", ["async def"]),
]

async def run_one(q):
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("deep_research",
                                    {"question": q, "max_loops": 2, "max_total_sources": 6})
            data = res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(data, dict) and "result" in data and "report_md" not in data:
                data = data["result"]
            return data or {}

async def main():
    cited = correct = total = 0
    rows = []
    for q, truths in CASES:
        total += 1
        try:
            d = await run_one(q)
        except Exception as e:
            rows.append((q, "ERR", 0, 0, f"{type(e).__name__}: {e}")); continue
        report = (d.get("report_md") or "").lower()
        ncite = len(d.get("citations") or [])
        is_cited = 1 if ncite >= 1 else 0
        is_correct = 1 if any(t.lower() in report for t in truths) else 0
        cited += is_cited; correct += is_correct
        rows.append((q, d.get("confidence", "?"), is_cited, is_correct, f"{ncite} cites, {d.get('sources_explored',0)} src"))
    print("\n  question                                          conf  cite  correct  detail")
    print("  " + "-" * 92)
    for q, conf, ci, co, det in rows:
        print(f"  {q[:48]:<48} {str(conf):<5} {ci:^4} {co:^7}  {det}")
    print("  " + "-" * 92)
    if total:
        print(f"\n  citation-correctness: {cited}/{total} ({100*cited//total}%)")
        print(f"  answer-correctness:   {correct}/{total} ({100*correct//total}%)  [substring proxy]")
    print("\n  NOTE: substring proxy on a tiny set — directional, not a benchmark.")
    print("  Open-local bar ~72-78% on simple factual; higher with a stronger/cloud chat model.")
    print("  If citations are high but answers low, RETRIEVAL is fine and SYNTHESIS is the")
    print("  bottleneck (the local model) — that argues for routing synthesis to escalation later.")

asyncio.run(main())
PY
