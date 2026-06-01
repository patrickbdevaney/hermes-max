"""hermes-max UI — Tier 1 web-tier backend.

A zero-dependency (Python stdlib only) localhost HTTP+SSE server that taps the
EXISTING telemetry — `lib.livelog` (the live tool-call JSONL) and
`lib.inference.ledger` (the $0.000000 cost ledger) — and rebroadcasts it as the
typed SSE event stream the React frontend (ui/web) renders as L0 ambient + L1
timeline + live cost. It adds NO new instrumentation; it only translates what the
agent already writes.

It implements the slice of the UI API contract that Tier 1 needs:
    GET  /api/status            GET  /api/cost
    GET  /api/config            GET  /api/projects/recent
    POST /api/run               GET  /api/events/{run_id}   (SSE)

Localhost hardening (bind 127.0.0.1, one-time launch token, Origin/Host checks,
CSRF token on POSTs) lives in security.py and is enforced from day one. Secrets
are never read or returned here — key capture is a Tier-2 concern.
"""
