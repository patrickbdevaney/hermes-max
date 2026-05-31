#!/usr/bin/env python3
"""condenser.py — context condenser (M-Stage 2), the OpenHands
LLMSummarizingCondenser mechanism: at ~80% context fill, summarize the OLDEST
events into one digest while always preserving the first `keep_first` messages and
the most recent turns. OpenHands reports up to ~2x token-cost reduction at this
fill with no measured quality loss (arXiv:2511.03690).

CLI:   echo '<history-json>' | condenser.py        # reads stdin, writes condensed JSON
Lib:   from condenser import condense               # used by the mcp-observability tool

`history` is a JSON list of {"role","content"} messages. Token count is estimated
(chars/4 — good enough to gate the threshold and report the ratio). Summarization
uses the local vLLM chat endpoint ($VLLM_BASE_URL); if it's unreachable the history
is returned UNCHANGED (never lose context to a failed summarizer).

CAVEAT (encoded in the skill too): condensing rewrites the prompt PREFIX and so
reduces prompt-cache hit rate — only condense when genuinely near the limit, not as
a routine optimization.

Stdlib only (json/os/sys/urllib) so it runs in any venv and as a bare script.
Emits a condenser_fired span to live.jsonl on firing.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
THRESHOLD_RATIO = float(os.environ.get("CONDENSER_THRESHOLD_RATIO", "0.80"))
KEEP_FIRST = int(os.environ.get("CONDENSER_KEEP_FIRST", "4"))
CONDENSE_OLDEST_RATIO = float(os.environ.get("CONDENSER_OLDEST_RATIO", "0.40"))
# Generous — the local reasoning model spends a large hidden thinking budget before
# the answer and returns content=None if max_tokens is too small (see memory:
# vllm-reasoning-model). 1024 is far too low; the digest itself is short.
SUMMARY_MAX_TOKENS = int(os.environ.get("CONDENSER_SUMMARY_MAX_TOKENS", "6000"))
LIVE_JSONL = os.path.expanduser(os.path.join(
    os.environ.get("HERMES_MAX_LOG_DIR", "~/.hermes-max/logs"), "live.jsonl"))

_SUMMARY_SYS = (
    "You compress conversation history for a long-running coding agent. Produce a "
    "DENSE digest of the messages below that preserves: decisions made, facts/values "
    "learned, files/symbols touched, errors hit, and OPEN threads still to do. Drop "
    "pleasantries and redundant restatement. Output ONLY the digest (no preamble)."
)


def _est_tokens(messages) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages) // 4


def _max_model_len() -> int | None:
    if not VLLM_BASE_URL:
        return None
    try:
        with urllib.request.urlopen(f"{VLLM_BASE_URL}/models", timeout=5) as r:
            data = json.loads(r.read()).get("data") or [{}]
        v = data[0].get("max_model_len")
        return int(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def _summarize(messages) -> str | None:
    if not VLLM_BASE_URL or not messages:
        return None
    blob = "\n\n".join(f"[{m.get('role','?')}] {str(m.get('content',''))}" for m in messages)[:24000]
    body = json.dumps({
        "model": os.environ.get("VLLM_MODEL", "/model"),
        "messages": [{"role": "system", "content": _SUMMARY_SYS},
                     {"role": "user", "content": blob}],
        "temperature": 0.1, "max_tokens": SUMMARY_MAX_TOKENS,
    }).encode()
    try:
        req = urllib.request.Request(f"{VLLM_BASE_URL}/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=float(os.environ.get("CONDENSER_TIMEOUT_S", "180"))) as r:
            content = json.loads(r.read())["choices"][0]["message"].get("content")
        return content.strip() if content else None
    except Exception:  # noqa: BLE001
        return None


def _emit_span(rec: dict) -> None:
    try:
        os.makedirs(os.path.dirname(LIVE_JSONL), exist_ok=True)
        with open(LIVE_JSONL, "a") as f:
            f.write(json.dumps({"ts": time.time(), "hms": time.strftime("%H:%M:%S"),
                                "kind": "span", **rec}, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass


def condense(history, threshold_ratio: float = THRESHOLD_RATIO, keep_first: int = KEEP_FIRST,
             condense_oldest_ratio: float = CONDENSE_OLDEST_RATIO,
             max_model_len: int | None = None, force: bool = False) -> dict:
    """Condense `history` if it exceeds threshold_ratio*max_model_len. Returns
    {fired, history, tokens_before, tokens_after, events_before, events_after, ratio}.
    Preserves the first keep_first messages + the most recent turns; summarizes the
    oldest condense_oldest_ratio of the middle. Unchanged (fired=False) if under
    threshold or if the summarizer is unavailable."""
    if not isinstance(history, list):
        history = []
    tokens_before = _est_tokens(history)
    events_before = len(history)
    mml = max_model_len or _max_model_len()
    budget = (threshold_ratio * mml) if mml else None

    if not force and (budget is None or tokens_before < budget):
        return {"fired": False, "history": history, "reason": (
            "under threshold" if budget else "max_model_len unknown — not condensing"),
            "tokens_before": tokens_before, "tokens_after": tokens_before,
            "events_before": events_before, "events_after": events_before, "ratio": 1.0,
            "max_model_len": mml, "threshold_tokens": int(budget) if budget else None}

    head = history[:keep_first]
    middle = history[keep_first:]
    n_old = int(len(middle) * condense_oldest_ratio)
    if n_old < 1:
        return {"fired": False, "history": history, "reason": "too few condensable events",
                "tokens_before": tokens_before, "tokens_after": tokens_before,
                "events_before": events_before, "events_after": events_before, "ratio": 1.0,
                "max_model_len": mml}
    oldest, recent = middle[:n_old], middle[n_old:]
    digest = _summarize(oldest)
    if not digest:
        return {"fired": False, "history": history, "reason": "summarizer unavailable — kept full history",
                "tokens_before": tokens_before, "tokens_after": tokens_before,
                "events_before": events_before, "events_after": events_before, "ratio": 1.0,
                "max_model_len": mml}

    summary_msg = {"role": "system",
                   "content": f"[CONDENSED HISTORY — {n_old} earlier events summarized]\n{digest}"}
    new_history = head + [summary_msg] + recent
    tokens_after = _est_tokens(new_history)
    ratio = round(tokens_after / tokens_before, 4) if tokens_before else 1.0
    rec = {"span": "condenser_fired", "events_before": events_before,
           "events_after": len(new_history), "tokens_before": tokens_before,
           "tokens_after": tokens_after, "ratio": ratio}
    _emit_span(rec)
    return {"fired": True, "history": new_history, **rec, "max_model_len": mml}


if __name__ == "__main__":
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else []
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"bad JSON on stdin: {e}"}))
        sys.exit(1)
    hist = payload.get("history", payload) if isinstance(payload, dict) else payload
    force = bool(payload.get("force")) if isinstance(payload, dict) else ("--force" in sys.argv)
    out = condense(hist, force=force)
    print(json.dumps(out))
