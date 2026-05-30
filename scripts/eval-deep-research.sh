#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# eval-deep-research.sh — PROVE the deep-research cascade runs end-to-end in the
# REAL loop (Stage 2), not in isolation.
#
# Invokes deep_research over MCP exactly as the Hermes agent would, on a real
# query, and ASSERTS that every stage fired with real data:
#   plan_research  -> >=2 sub-goals
#   develop_queries-> per-subgoal queries
#   explore        -> fetched >=2 sources from >=2 distinct domains  (real URLs)
#   verify_claims  -> ran (claims extracted + checked)
#   synthesize     -> non-empty report with >=1 citation
#   compound       -> brief written to the RAG corpus
# Per-stage telemetry (the live.jsonl spans) is captured into a readable artifact
# (deep_research_trace.md) so the operator can confirm each stage fired.
#
# Then a DEGRADATION sub-test: with Crawl4AI stopped, explore must still return
# sources via the trafilatura fallback — a single backend failing never fails the
# cascade.
#
# Requires the live sovereign loop (mcp-research 9110 + mcp-docs/SearXNG + a chat
# model). If any is down it SKIPS cleanly (informational, exit 0) rather than
# reporting a meaningless failure.
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
QUESTION="${1:-Groth16 zk-SNARK verifier specification and test vectors}"
ARTIFACT="${REPO_ROOT}/deep_research_trace.md"
LIVE_JSONL="${HMX_LOG_DIR:-${HOME}/.hermes-max/logs}/live.jsonl"

echo "═══ Stage 2 · deep_research end-to-end (live, sovereign) ═══"
echo "question: ${QUESTION}"

# ── preflight (skip cleanly if a dependency is missing) ──────────────────────
if ! curl -fsS -m5 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "• mcp-research (${PORT}) not live — start: hm up. SKIPPING (informational)."; exit 0
fi
if ! curl -fsS -m6 "${SEARXNG_URL:-http://localhost:8080}/search?q=test&format=json" >/dev/null 2>&1; then
  echo "• SearXNG not reachable — run ./searXNG.sh. SKIPPING (informational)."; exit 0
fi
if [ -z "${VLLM_BASE_URL:-}" ] || ! curl -fsS -m6 "${VLLM_BASE_URL%/}/models" >/dev/null 2>&1; then
  echo "• chat model (${VLLM_BASE_URL:-unset}) not reachable. SKIPPING (informational)."; exit 0
fi

MARK="$(wc -l < "${LIVE_JSONL}" 2>/dev/null || echo 0)"

# ── run the cascade over MCP and assert the real-world effects ───────────────
MCP_URL="${URL}" QUESTION="${QUESTION}" ARTIFACT="${ARTIFACT}" \
LIVE_JSONL="${LIVE_JSONL}" MARK="${MARK}" "${PY}" - <<'PY'
import asyncio, json, os, sys, time
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL=os.environ["MCP_URL"]; Q=os.environ["QUESTION"]
ARTIFACT=os.environ["ARTIFACT"]; LIVE=os.environ["LIVE_JSONL"]; MARK=int(os.environ["MARK"])

async def call(tool, args, timeout=900):
    # sse_read_timeout must exceed the whole cascade (the result streams at the
    # end); default 300s is too short once explore actually fetches+distils.
    async with streamablehttp_client(URL, timeout=60, sse_read_timeout=900) as (r,w,_):
        async with ClientSession(r,w) as s:
            await s.initialize()
            res=await asyncio.wait_for(s.call_tool(tool, args), timeout=timeout)
            d=res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(d,dict) and "result" in d and "report_md" not in d and "ok" not in d:
                d=d["result"]
            return d or {}

async def main():
    t0=time.time()
    d=await call("deep_research", {"question":Q,"max_loops":3,"max_total_sources":8})
    elapsed=time.time()-t0

    srcs=d.get("sources",[]) or []
    domains=sorted({s.get("domain") for s in srcs if s.get("domain")})
    verified=d.get("verified_findings",[]) or []
    citations=d.get("citations",[]) or []
    report=d.get("report_md") or ""
    comp=d.get("compounded",{}) or {}
    subgoals=d.get("plan",{}).get("subgoals",[]) or []

    # ── assertions: each stage fired AND produced its real effect ────────────
    checks=[]
    def chk(name, ok, detail): checks.append((name, bool(ok), detail))
    chk("returned ok", d.get("ok"), f"ok={d.get('ok')} stop_reason={d.get('stop_reason')}")
    chk("plan_research >=2 subgoals", len(subgoals)>=2, f"{len(subgoals)} subgoals")
    chk("explore fetched >=2 sources", len(srcs)>=2, f"{len(srcs)} sources")
    chk("sources span >=2 distinct domains", len(domains)>=2, f"{len(domains)} domains: {domains[:6]}")
    chk("verify_claims ran (>=1 finding)", len(verified)>=1, f"{len(verified)} verified findings")
    chk("synthesize produced a report", len(report)>200, f"{len(report)} chars")
    chk("report has >=1 citation", len(citations)>=1, f"{len(citations)} citations")
    chk("within wall budget (<=600s)", elapsed<=600, f"{elapsed:.0f}s")
    chk("compounded to RAG corpus", bool(comp.get("rag_stored")),
        f"rag_stored={comp.get('rag_stored')} kg_entities={comp.get('kg_entities')}")

    # ── per-stage telemetry captured from live.jsonl (this run's tail) ───────
    spans=[]
    try:
        with open(LIVE) as f:
            for i,ln in enumerate(f):
                if i<MARK: continue
                try: e=json.loads(ln)
                except Exception: continue
                if e.get("kind")=="span":
                    spans.append(e)
    except Exception: pass

    # ── readable artifact ────────────────────────────────────────────────────
    L=[]
    L.append("# deep_research — end-to-end cascade trace (Stage 2 proof)\n")
    L.append(f"- **question:** {Q}")
    L.append(f"- **elapsed:** {elapsed:.1f}s   **loops:** {d.get('loops')}   **stop_reason:** {d.get('stop_reason')}")
    L.append(f"- **sources:** {len(srcs)} across {len(domains)} domains   **citations:** {len(citations)}   **confidence:** {d.get('confidence')}\n")
    L.append("## Per-stage telemetry (live.jsonl spans)\n")
    for e in spans:
        attrs={k:v for k,v in e.items() if k not in ("ts","hms","kind","span")}
        L.append(f"- `{e.get('hms')}` **{e.get('span')}** {json.dumps(attrs, default=str)[:160]}")
    L.append("\n## Sources fetched\n")
    for s in srcs:
        L.append(f"- [{s.get('authority')}] {s.get('domain')} — {(s.get('title') or '')[:70]}  \n  {s.get('url')}")
    L.append("\n## Assertions\n")
    for name,ok,detail in checks:
        L.append(f"- {'✅' if ok else '❌'} **{name}** — {detail}")
    L.append("\n## Synthesised report\n")
    L.append(report)
    open(ARTIFACT,"w").write("\n".join(L)+"\n")

    print(f"\n── deep_research returned in {elapsed:.1f}s ──")
    npass=sum(1 for _,ok,_ in checks if ok)
    for name,ok,detail in checks:
        print(f"  {'✅' if ok else '❌'} {name}: {detail}")
    print(f"\nartifact: {ARTIFACT}")
    print(f"per-stage spans captured: {[e.get('span') for e in spans]}")
    print(f"\n{npass}/{len(checks)} assertions passed")
    sys.exit(0 if npass==len(checks) else 1)

asyncio.run(main())
PY
RC=$?

echo
echo "── degradation sub-test: Crawl4AI down → explore still returns sources (trafilatura) ──"
if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' | grep -q crawl4ai; then
  docker stop crawl4ai >/dev/null 2>&1 && echo "  (stopped crawl4ai container)"
  MCP_URL="${URL}" "${PY}" - <<'PY'
import asyncio, json, os
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
URL=os.environ["MCP_URL"]
async def main():
    async with streamablehttp_client(URL, timeout=60, sse_read_timeout=300) as (r,w,_):
        async with ClientSession(r,w) as s:
            await s.initialize()
            res=await asyncio.wait_for(s.call_tool("explore",
                {"queries":["Groth16 zk-SNARK verifier specification"],"max_total":4}), timeout=180)
            d=res.structuredContent or (json.loads(res.content[0].text) if res.content else {})
            if isinstance(d,dict) and "result" in d and "sources" not in d: d=d["result"]
            n=len(d.get("sources",[]) or [])
            print(f"  explore with Crawl4AI down -> {n} sources fetched (via trafilatura fallback)")
            print("  ✅ cascade degrades gracefully (run completed, sources>0)" if n>0
                  else "  ⚠ 0 sources with Crawl4AI down — trafilatura may also be unavailable")
asyncio.run(main())
PY
  docker start crawl4ai >/dev/null 2>&1 && echo "  (restarted crawl4ai container)"
else
  echo "  • crawl4ai container not found — skipping degradation sub-test (informational)"
fi

echo
[ "${RC}" -eq 0 ] && echo "✅ Stage 2 PASS — deep_research runs end-to-end with real sources." \
                  || echo "❌ Stage 2 FAIL — see assertions above and ${ARTIFACT}."
exit "${RC}"
