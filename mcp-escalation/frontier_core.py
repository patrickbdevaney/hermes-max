"""frontier_core — the SPARING Opus 4.8 frontier-escalation tier (the `--frontier`
top rung), engineered so Opus stays RARE enough to preserve the affordability Pareto.

Opus 4.8 (`claude-opus-4-8`, $5/M in, $25/M out, regular mode) fires ONLY when ALL
THREE gates trip:
  1. MODE+KEY    — CONDUCTOR_MODE=frontier AND ANTHROPIC_API_KEY present.
  2. DIFFICULTY  — the classifier flags the subtask FRONTIER-NOVEL (genuinely
                   blue-ocean: novelty=high AND a no-reference / blue-ocean signal).
                   Merely-HARD-but-known stays at V4-Pro.
  3. FAILURE     — V4-Pro synth has ALREADY failed the verify gate TWICE on this
                   subtask, OR two independent V4-Pro opinions disagree on a
                   high-blast-radius change. Opus is the tie-breaker / last resort.

When all three trip, COMPRESS-THEN-REASON keeps the call cheap even when used:
  (a) V4-Pro (the cheap model) compresses the full situation into a dense ~12K
      brief — the cheap model does the expensive-to-Opus token compression;
  (b) Opus 4.8 reasons on that distilled brief and returns the frontier plan.
  ⇒ ~$0.18/call (→ ~$0.10 with prompt caching), vs ~$0.40 if Opus ingested raw.

PLAN-TO-ARTIFACT: Opus's plan is written to a durable FRONTIER_PLAN.md AND ingested
into RAG/KG with provenance (source=opus-4.8, the problem, the date) — so the
frontier reasoning compounds and is never paid for twice. The plan is ADVISORY:
it passes through directive_verify before the driver executes (Opus is expensive,
not trusted-blind). A hard frontier USD cap (monthly/daily) blocks + falls back to
V4-Pro synth when hit. Every Opus invocation is logged with its three-gate
justification. Never raises — degrades to V4-Pro/local on any failure.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any

import conductor_core as cc
import conductor_resolver as resolver
import directive_verify as dv
import escalation_core
from brief_assemble import KG_PORT, RAG_PORT, _mcp

ANTHROPIC = "anthropic"

# Frontier-specific caps + sparing target (separate from the general conductor cap
# so the frontier tier governs its own spend). Defaults per the spec.
FRONTIER_USD_CAP_MONTHLY = float(os.environ.get("FRONTIER_USD_CAP_MONTHLY", "10"))
FRONTIER_USD_CAP_DAILY = float(os.environ.get("FRONTIER_USD_CAP_DAILY", "2"))
FRONTIER_TARGET_CALLS_MONTHLY = int(os.environ.get("FRONTIER_TARGET_CALLS_MONTHLY", "15"))
COMPRESS_MAX_TOKENS = int(os.environ.get("FRONTIER_COMPRESS_MAX_TOKENS", "4096"))   # V4-Pro output (the brief)
OPUS_MAX_TOKENS = int(os.environ.get("FRONTIER_OPUS_MAX_TOKENS", "4096"))           # Opus output (the plan)
RAW_SITUATION_CAP_CHARS = int(os.environ.get("FRONTIER_RAW_SITUATION_CAP", "48000"))

FRONTIER_STATE = os.path.expanduser(
    os.environ.get("FRONTIER_STATE_PATH", "~/.hermes-max/conductor/frontier.json"))
FRONTIER_PLAN_NAME = os.environ.get("FRONTIER_PLAN_FILENAME", "FRONTIER_PLAN.md")

_lock = threading.Lock()

_COMPRESS_SYS = (
    "You are a senior staff engineer preparing a brief for a FRONTIER reasoner that "
    "will see ONLY your brief — never the raw repo. Compress the SITUATION below into "
    "the most information-dense, precise, self-contained brief possible (aim ≤ ~12K "
    "tokens): the EXACT frontier problem, the hard constraints, what was ALREADY tried "
    "and precisely why each attempt failed, the MINIMAL relevant code/architecture, and "
    "the specific decision that must be made. Omit anything the reasoner does not need. "
    "NEVER include secrets, API keys, or tokens. Output ONLY the brief."
)
_FRONTIER_SYS = (
    "You are reasoning on a genuinely blue-ocean, FRONTIER-NOVEL engineering problem that "
    "cheaper models could not close. You see ONLY the distilled brief below. Produce a "
    "concrete FRONTIER PLAN as JSON with keys: `approach` (the novel architecture/insight), "
    "`steps` (ordered directive the executing agent follows), `assumptions` (each a short "
    "string, checkable against repo state), `apis_to_use` (symbols/files), and "
    "`tests_to_write` (concrete objective oracles). Be decisive and specific. Output ONLY the JSON."
)


# ── frontier spend state (own file; feeds the cap + the sparing report) ───────
def _blank_state() -> dict[str, Any]:
    t = date.today().isoformat()
    return {"date": t, "month": t[:7], "calls_today": 0, "calls_month": 0,
            "spend_today": 0.0, "spend_month": 0.0, "calls_total": 0, "spend_total": 0.0}


def _load_state() -> dict[str, Any]:
    t = date.today().isoformat()
    m = t[:7]
    try:
        with open(FRONTIER_STATE) as f:
            st = json.load(f)
    except Exception:  # noqa: BLE001 - missing/corrupt -> fresh
        return _blank_state()
    if st.get("date") != t:
        st["date"] = t
        st["calls_today"] = 0
        st["spend_today"] = 0.0
    if st.get("month") != m:
        st["month"] = m
        st["calls_month"] = 0
        st["spend_month"] = 0.0
    for k, v in _blank_state().items():
        st.setdefault(k, v)
    return st


def _save_state(st: dict[str, Any]) -> None:
    Path(FRONTIER_STATE).parent.mkdir(parents=True, exist_ok=True)
    tmp = FRONTIER_STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, FRONTIER_STATE)


def _record_opus(cost: float) -> dict[str, Any]:
    with _lock:
        st = _load_state()
        st["calls_today"] += 1
        st["calls_month"] += 1
        st["calls_total"] += 1
        st["spend_today"] = round(st["spend_today"] + cost, 6)
        st["spend_month"] = round(st["spend_month"] + cost, 6)
        st["spend_total"] = round(st.get("spend_total", 0.0) + cost, 6)
        _save_state(st)
        return st


def _cap_blocked(st: dict[str, Any]) -> str | None:
    if st["spend_month"] >= FRONTIER_USD_CAP_MONTHLY:
        return (f"frontier MONTHLY USD cap reached "
                f"(${st['spend_month']:.4f} >= ${FRONTIER_USD_CAP_MONTHLY})")
    if st["spend_today"] >= FRONTIER_USD_CAP_DAILY:
        return (f"frontier DAILY USD cap reached "
                f"(${st['spend_today']:.4f} >= ${FRONTIER_USD_CAP_DAILY})")
    return None


def _otel(name: str, attrs: dict[str, Any]) -> None:
    try:
        import otel_emit

        otel_emit.record(name, attrs, status="ok")
    except Exception:  # noqa: BLE001 - observability optional
        pass


# ── GATE 2: the frontier-novel classifier ─────────────────────────────────────
def _novelty_high(novelty: Any) -> bool:
    if isinstance(novelty, (int, float)):
        return novelty >= 0.66
    return str(novelty or "").strip().lower() in ("high", "frontier", "novel")


def classify_frontier(signals: dict | None = None) -> dict[str, Any]:
    """FRONTIER-NOVEL iff genuinely blue-ocean: novelty=high AND an explicit
    no-reference / blue-ocean signal. Merely-HARD (many files, prior failures,
    big diff) is NOT frontier-novel by itself — it stays at V4-Pro. This is the
    difficulty gate: it deliberately requires an explicit blue-ocean flag so the
    classifier under-escalates rather than mis-flagging hard-but-known as frontier."""
    s = signals or {}
    base = escalation_core.classify_difficulty(s)
    nv_high = _novelty_high(s.get("novelty"))
    blue = bool(s.get("blue_ocean") or s.get("no_reference_impl") or s.get("frontier_novel"))
    frontier_novel = nv_high and blue
    reasons: list[str] = []
    reasons.append("novelty=high" if nv_high else f"novelty not high ({s.get('novelty')!r})")
    reasons.append("blue-ocean / no-reference-implementation signal present" if blue
                   else "no blue-ocean/no-reference signal — treat as merely HARD (V4-Pro)")
    return {"ok": True, "frontier_novel": frontier_novel, "difficulty": base["difficulty"],
            "reasons": reasons}


# ── situation assembly + artifact + directive parsing ─────────────────────────
def _situation(task: str, context: Any) -> str:
    parts = [f"## Frontier problem\n{task}"]
    if isinstance(context, str) and context.strip():
        parts.append(f"## Context\n{context}")
    elif isinstance(context, dict):
        for key in ("plan", "architecture", "diffs", "failed_approaches",
                    "failure_traces", "traces", "code", "code_excerpts", "notes"):
            val = context.get(key)
            if val:
                parts.append(f"### {key}\n{val}")
    return "\n\n".join(parts)


def _slug(text: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (text or "frontier").lower()).strip("-")
    return (s[:60] or "frontier")


def _plan_to_directive(plan_md: str, task: str) -> dict[str, Any]:
    """Best-effort parse Opus's plan into a directive dict for directive_verify.
    Opus is asked for JSON; if it returns prose we still pass a directive through
    the gate (assumptions/apis empty → the gate reports what's unverifiable)."""
    txt = (plan_md or "").strip()
    # pull the first JSON object if present (handles ```json fences / prose wrap)
    import re
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                d.setdefault("steps", d.get("approach", ""))
                d.setdefault("assumptions", [])
                d.setdefault("apis_to_use", [])
                d.setdefault("tests_to_write", [])
                return d
        except Exception:  # noqa: BLE001
            pass
    return {"steps": txt, "assumptions": [], "apis_to_use": [], "tests_to_write": [],
            "_note": "Opus returned prose, not JSON — directive gated as best-effort"}


def _write_artifact(task: str, plan_md: str, brief: str, three_gate: dict[str, Any],
                    repo: str | None, compress_meta: dict[str, Any],
                    opus: dict[str, Any]) -> dict[str, Any]:
    """Write FRONTIER_PLAN.md (durable, in-project) AND ingest into RAG + KG with
    provenance (source=opus-4.8, the problem, the date). Best-effort; never raises."""
    today = date.today().isoformat()
    slug = _slug(task)
    header = (f"# FRONTIER_PLAN — {task}\n\n"
              f"- **source:** {opus.get('model', 'claude-opus-4-8')} (frontier escalation)\n"
              f"- **date:** {today}\n"
              f"- **three-gate justification:** {json.dumps(three_gate)}\n"
              f"- **compress-then-reason:** {json.dumps(compress_meta)}\n"
              f"- **opus cost_usd:** {opus.get('cost_usd')}\n\n"
              f"## Distilled brief (V4-Pro compressed)\n\n{brief}\n\n"
              f"## Frontier plan (Opus 4.8)\n\n{plan_md}\n")
    out: dict[str, Any] = {"plan_path": None, "rag_stored": False, "kg_recorded": False}
    # 1) durable file in the project
    try:
        base = repo or os.environ.get("FRONTIER_PLAN_DIR") or os.getcwd()
        path = os.path.join(base, FRONTIER_PLAN_NAME)
        with open(path, "w") as f:
            f.write(header)
        out["plan_path"] = path
    except Exception as e:  # noqa: BLE001
        out["plan_error"] = f"{type(e).__name__}: {e}"
    # 2) RAG (co-retrievable with code) with provenance
    rag = _mcp(RAG_PORT, "index_document",
               {"text": header, "namespace": f"frontier/{slug}",
                "source": "opus-4.8", "title": task[:80]})
    out["rag_stored"] = bool(rag and rag.get("ok"))
    # 3) KG entity with provenance
    kg = _mcp(KG_PORT, "record_entity",
              {"type": "frontier_plan", "name": f"frontier:{slug}",
               "props": {"source": "opus-4.8", "problem": task[:200], "date": today,
                         "cost_usd": opus.get("cost_usd"), "plan_path": out["plan_path"]}})
    out["kg_recorded"] = bool(kg and kg.get("ok"))
    return out


# ── the three-gated compress-then-reason frontier escalation ──────────────────
def frontier_escalate(task: str, *, signals: dict | None = None, context: Any = None,
                      repo: str | None = None, task_id: str | None = None,
                      synth_failures: int = 0, opinions_disagree: bool = False,
                      blast_radius: str | None = None,
                      compressed_brief: str | None = None) -> dict[str, Any]:
    """Try to escalate a FRONTIER-NOVEL, twice-failed subtask to Opus 4.8 via
    compress-then-reason. Returns a dict ALWAYS (never raises). opus_invoked=True
    only when all three gates trip AND the cap allows; otherwise returns the gate
    that failed and the route it falls back to (V4-Pro synth / lower mode)."""
    env = dict(os.environ)
    mode = resolver.current_mode(env)
    has_key = bool((env.get("ANTHROPIC_API_KEY") or "").strip())
    signals = signals or {}
    blast = (blast_radius or ("high" if signals.get("cross_module")
             or int(signals.get("file_count", 0) or 0) >= 4 else "low"))

    # ── GATE 1: mode + key ────────────────────────────────────────────────────
    if mode != "frontier":
        return {"ok": True, "opus_invoked": False, "gate_failed": "mode",
                "route": "v4-pro-synth", "active_mode": mode,
                "reason": f"frontier tier requires --frontier mode (active mode={mode!r}); "
                          "staying on the current tier ceiling"}
    if not has_key:
        return {"ok": True, "opus_invoked": False, "gate_failed": "key",
                "route": "v4-pro-synth", "fell_back_to": "full",
                "warning": "--frontier requested but ANTHROPIC_API_KEY is absent — "
                           "falling back to --full behavior (Opus tier OFF)"}

    # ── GATE 2: frontier-novel (not merely HARD) ──────────────────────────────
    fr = classify_frontier(signals)
    if not fr["frontier_novel"]:
        return {"ok": True, "opus_invoked": False, "gate_failed": "difficulty",
                "route": "v4-pro-synth", "classifier": fr,
                "reason": "subtask is not FRONTIER-NOVEL (merely HARD/known) — stays at V4-Pro synth"}

    # ── GATE 3: failure gate (last-resort only) ───────────────────────────────
    failure_ok = (int(synth_failures) >= 2) or (bool(opinions_disagree) and blast == "high")
    if not failure_ok:
        return {"ok": True, "opus_invoked": False, "gate_failed": "failure",
                "route": "v4-pro-synth",
                "reason": "Opus is last-resort: needs V4-Pro to have failed verify TWICE "
                          f"(synth_failures={synth_failures}) OR two opinions to disagree on a "
                          f"high-blast change (opinions_disagree={opinions_disagree}, blast={blast})"}

    three_gate = {"mode": mode, "key_present": True, "frontier_novel": True,
                  "classifier_reasons": fr["reasons"], "synth_failures": int(synth_failures),
                  "opinions_disagree": bool(opinions_disagree), "blast_radius": blast}

    # ── COMPRESS (V4-Pro writes the dense ~12K brief) ─────────────────────────
    brief = compressed_brief
    compress_meta: dict[str, Any] = {}
    if not brief:
        situation = _situation(task, context)
        comp = cc.run_role("synth", prompt=f"{_COMPRESS_SYS}\n\n## SITUATION\n{situation}",
                           max_tokens=COMPRESS_MAX_TOKENS)
        if comp.get("ok") and comp.get("content"):
            brief = comp["content"]
            compress_meta = {"compressor": comp.get("provider"), "model": comp.get("model"),
                             "cost_usd": comp.get("cost_usd"),
                             "brief_tokens_est": len(brief) // 4}
        else:
            # synth unavailable -> degrade: use the raw situation (bounded) as the brief
            brief = situation[:RAW_SITUATION_CAP_CHARS]
            compress_meta = {"compressor": "(synth unavailable — raw situation, truncated)",
                             "brief_tokens_est": len(brief) // 4}
    else:
        compress_meta = {"compressor": "(caller-supplied brief)", "brief_tokens_est": len(brief) // 4}

    # ── frontier USD cap: hit -> DO NOT call Opus, fall back to V4-Pro synth ──
    st = _load_state()
    capped = _cap_blocked(st)
    if capped:
        fb = cc.run_role("synth", prompt=f"{_FRONTIER_SYS}\n\n## BRIEF\n{brief}",
                         max_tokens=OPUS_MAX_TOKENS)
        _otel("frontier_capped", {"reason": capped, "spend_month": st["spend_month"]})
        return {"ok": True, "opus_invoked": False, "capped": True, "route": "v4-pro-synth-fallback",
                "three_gate": three_gate, "compress": compress_meta, "cap_reason": capped,
                "fallback_result": {"provider": fb.get("provider"), "content": fb.get("content"),
                                    "cost_usd": fb.get("cost_usd")},
                "frontier_spend": _spend_view(st),
                "reason": "frontier USD cap reached — fell back to V4-Pro synth (logged)"}

    # ── REASON (Opus 4.8 on the compressed brief) ─────────────────────────────
    op = cc.call_one(ANTHROPIC, "escalate", prompt=f"{_FRONTIER_SYS}\n\n## BRIEF\n{brief}",
                     max_tokens=OPUS_MAX_TOKENS)
    if not op.get("ok"):
        fb = cc.run_role("synth", prompt=f"{_FRONTIER_SYS}\n\n## BRIEF\n{brief}",
                         max_tokens=OPUS_MAX_TOKENS)
        return {"ok": True, "opus_invoked": False, "route": "v4-pro-synth-fallback",
                "three_gate": three_gate, "compress": compress_meta,
                "opus_error": op.get("reason"),
                "fallback_result": {"provider": fb.get("provider"), "content": fb.get("content"),
                                    "cost_usd": fb.get("cost_usd")},
                "reason": "Opus call failed/unavailable — fell back to V4-Pro synth"}

    plan_md = op.get("content") or ""
    cost = float(op.get("cost_usd", 0.0) or 0.0)
    st = _record_opus(cost)

    # ── PLAN-TO-ARTIFACT (durable + RAG/KG with provenance) ───────────────────
    artifact = _write_artifact(task, plan_md, brief, three_gate, repo, compress_meta, op)

    # ── VERIFY-GATE the directive (advisory; Opus is expensive, not trusted-blind) ─
    directive = _plan_to_directive(plan_md, task)
    try:
        verify = dv.directive_verify(directive, repo=repo, task_id=task_id, run_static=False)
    except Exception as e:  # noqa: BLE001
        verify = {"execute": False, "error": f"{type(e).__name__}: {e}"}

    _otel("frontier_opus_invoked", {"model": op.get("model"), "cost_usd": round(cost, 6),
          "brief_tokens_est": compress_meta.get("brief_tokens_est"),
          "synth_failures": int(synth_failures), "opinions_disagree": bool(opinions_disagree),
          "calls_month": st["calls_month"], "spend_month": st["spend_month"]})

    return {"ok": True, "opus_invoked": True, "model": op.get("model"),
            "three_gate": three_gate, "compress": compress_meta,
            "brief_tokens_est": compress_meta.get("brief_tokens_est"),
            "plan_md": plan_md, "cost_usd": round(cost, 6), "usage": op.get("usage"),
            "artifact": artifact,
            "directive_verify": {"execute": verify.get("execute"),
                                 "gates": {k: verify.get(k) for k in
                                           ("assumption_check", "static_gate", "test_gate",
                                            "confidence") if k in verify}},
            "frontier_spend": _spend_view(st),
            "reason": "frontier-novel + twice-failed: Opus reasoned on the V4-Pro-compressed "
                      "brief (compress-then-reason); plan written to artifact + RAG/KG; verify-gated"}


def _spend_view(st: dict[str, Any]) -> dict[str, Any]:
    return {"calls_today": st["calls_today"], "calls_month": st["calls_month"],
            "calls_total": st["calls_total"],
            "spend_today_usd": round(st["spend_today"], 4),
            "spend_month_usd": round(st["spend_month"], 4),
            "cap_daily_usd": FRONTIER_USD_CAP_DAILY, "cap_monthly_usd": FRONTIER_USD_CAP_MONTHLY,
            "target_calls_monthly": FRONTIER_TARGET_CALLS_MONTHLY,
            "calls_vs_target": f"{st['calls_month']}/{FRONTIER_TARGET_CALLS_MONTHLY}"}


def frontier_status() -> dict[str, Any]:
    """Mode/key state + month-to-date Opus spend vs cap + calls vs sparing target."""
    env = dict(os.environ)
    mode = resolver.current_mode(env)
    has_key = bool((env.get("ANTHROPIC_API_KEY") or "").strip())
    st = _load_state()
    return {"ok": True, "mode": mode, "frontier_eligible": (mode == "frontier" and has_key),
            "anthropic_key_present": has_key, "model": "claude-opus-4-8",
            **_spend_view(st),
            "over_target": st["calls_month"] > FRONTIER_TARGET_CALLS_MONTHLY,
            "cap_blocked": _cap_blocked(st)}
