"""Conductor role executor + cost ledger — piece (c) of the three-piece router.

Two execution shapes, both presence-gated and both NEVER-RAISE:

  • run_role(role, ...) — ORDERED chains (steer/synth/escalate). Walk the present
    rungs; call the first; on failure/429/5xx/timeout/deprecation SILENTLY advance
    and log a one-line trace entry; if none succeed (or all paid rungs are blocked
    by the USD cap) return a graceful {proceed_local: True} signal. It is a hard
    invariant that this returns a dict and never propagates an exception into the
    local orchestrator's core loop.

  • draft_fanout(...) — the UNORDERED parallel_draft pool. Fan out concurrently to
    every present pool member that is within its live RPM/RPD budget; skip
    exhausted sources; degrade to fewer candidates (or N=1-local) rather than
    failing. Returns the raw candidates — the VERIFIER (mcp-search, Stage 4), not
    this module, selects the winner.

Cost is metered to a conductor ledger (separate from mcp-escalation's spend.json
so neither perturbs the other) with per-day + per-month caps; once a cap is hit,
paid rungs behave as if absent. The free Opus escalate rung still routes through
mcp-escalation's own capped server — this module never calls Opus directly.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

import conductor_registry as reg
import conductor_resolver as resolver

TIMEOUT = float(os.environ.get("CONDUCTOR_TIMEOUT", "90"))
MAX_TOKENS = int(os.environ.get("CONDUCTOR_MAX_TOKENS", "4096"))
LEDGER_PATH = os.path.expanduser(
    os.environ.get("CONDUCTOR_LEDGER_PATH", "~/.hermes-max/conductor/ledger.json"))
BUDGET_PATH = os.path.expanduser(
    os.environ.get("CONDUCTOR_BUDGET_PATH", "~/.hermes-max/conductor/budget.json"))

_lock = threading.Lock()
# rolling in-memory trace of silent falls (also emitted as OTel spans); the
# Stage-5 report and `status()` read it. Bounded.
_TRACE: list[dict[str, Any]] = []
_TRACE_MAX = 200


def _otel(name: str, attrs: dict[str, Any]) -> None:
    try:
        import otel_emit

        otel_emit.record(name, attrs, status="ok")
    except Exception:  # noqa: BLE001 - observability optional
        pass


def _trace(event: str, **attrs: Any) -> None:
    rec = {"event": event, **attrs}
    with _lock:
        _TRACE.append(rec)
        if len(_TRACE) > _TRACE_MAX:
            del _TRACE[: len(_TRACE) - _TRACE_MAX]
    _otel(event, attrs)


# ── cost ledger (per-day + per-month, per-provider/role) ──────────────────────
def _blank_ledger() -> dict[str, Any]:
    return {"date": date.today().isoformat(), "month": date.today().isoformat()[:7],
            "spend_today": 0.0, "spend_month": 0.0, "calls": 0,
            "by_provider": {}, "by_role": {}}


def _load_ledger() -> dict[str, Any]:
    today = date.today().isoformat()
    month = today[:7]
    try:
        with open(LEDGER_PATH) as f:
            lg = json.load(f)
    except Exception:  # noqa: BLE001 - missing/corrupt -> fresh
        return _blank_ledger()
    if lg.get("date") != today:
        lg["date"] = today
        lg["spend_today"] = 0.0
    if lg.get("month") != month:
        lg["month"] = month
        lg["spend_month"] = 0.0
        lg["by_provider"] = {}
        lg["by_role"] = {}
    lg.setdefault("by_provider", {})
    lg.setdefault("by_role", {})
    lg.setdefault("calls", 0)
    return lg


def _save_ledger(lg: dict[str, Any]) -> None:
    Path(LEDGER_PATH).parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lg, f)
    os.replace(tmp, LEDGER_PATH)


def _record_cost(provider: str, role: str, usd: float) -> dict[str, Any]:
    with _lock:
        lg = _load_ledger()
        lg["spend_today"] += usd
        lg["spend_month"] += usd
        lg["calls"] += 1
        lg["by_provider"][provider] = round(lg["by_provider"].get(provider, 0.0) + usd, 6)
        lg["by_role"][role] = round(lg["by_role"].get(role, 0.0) + usd, 6)
        _save_ledger(lg)
        return lg


def _cap_blocked(caps: dict[str, float], lg: dict[str, Any]) -> str | None:
    if lg["spend_today"] >= caps.get("usd_daily", 1.0):
        return f"daily USD cap reached (${lg['spend_today']:.4f} >= ${caps['usd_daily']})"
    if lg["spend_month"] >= caps.get("usd_monthly", 5.0):
        return f"monthly USD cap reached (${lg['spend_month']:.4f} >= ${caps['usd_monthly']})"
    return None


# ── per-(provider,model) RPM/RPD/TPM budget (PRE-FLIGHT, header-fed) ──────────
# Free-tier TPM (tokens-per-minute) is the BINDING limit and is per-MODEL on Groq
# (gpt-oss-120b 8K, qwen3-32b 6K): a single 6K-token brief eats the whole minute.
# So we estimate a call's token footprint and SKIP a rung BEFORE firing if it would
# exceed the remaining TPM — never absorbing a 429. Budgets seed from the registry
# and are corrected live from each response's x-ratelimit-remaining/-reset headers.
import re  # noqa: E402

CHARS_PER_TOKEN = 4  # conservative heuristic; no tokenizer dependency


def _est_tokens(messages: list[dict]) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages) // CHARS_PER_TOKEN + 4


def _limits_for(prov_cfg: dict[str, Any], model: str) -> dict[str, Any]:
    tpm = (prov_cfg.get("model_tpm") or {}).get(model, prov_cfg.get("tpm"))
    return {"rpm": prov_cfg.get("rpm"), "rpd": prov_cfg.get("rpd"), "tpm": tpm}


def _parse_reset(val: Any) -> float | None:
    """Groq/OpenAI reset headers look like '6.5s', '1m30s', '2m', or bare seconds.
    Return seconds-from-now, or None if unparseable."""
    if val is None:
        return None
    s = str(val).strip()
    try:
        return float(s)  # bare seconds
    except ValueError:
        pass
    total = 0.0
    matched = False
    for num, unit in re.findall(r"([\d.]+)\s*(ms|s|m|h)", s):
        matched = True
        n = float(num)
        total += {"ms": n / 1000, "s": n, "m": n * 60, "h": n * 3600}[unit]
    return total if matched else None


def _save_budget(buckets: dict[str, Any]) -> None:
    Path(BUDGET_PATH).parent.mkdir(parents=True, exist_ok=True)
    tmp = BUDGET_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(buckets, f)
    os.replace(tmp, BUDGET_PATH)


def _budget_check(provider: str, model: str, prov_cfg: dict[str, Any],
                  est_tokens: int, *, commit: bool) -> tuple[bool, str]:
    """PRE-FLIGHT gate. Returns (ok, reason) where reason in {ok, rpm, rpd, tpm}.
    Unlimited providers (no rpm/rpd/tpm) short-circuit to (True, 'ok') with no I/O.
    A live header snapshot (remaining tokens + reset) overrides the local estimate
    while it is fresh."""
    lim = _limits_for(prov_cfg, model)
    if not lim["rpm"] and not lim["rpd"] and not lim["tpm"]:
        return True, "ok"
    key = f"{provider}:{model}"
    now = time.time()
    with _lock:
        try:
            with open(BUDGET_PATH) as f:
                buckets = json.load(f)
        except Exception:  # noqa: BLE001
            buckets = {}
        b = buckets.get(key, {})
        req = [t for t in b.get("req", []) if now - t < 86_400]
        tok = [e for e in b.get("tok", []) if now - e[0] < 60]
        if lim["rpm"] and sum(1 for t in req if now - t < 60) >= lim["rpm"]:
            b["req"], b["tok"] = req, tok
            buckets[key] = b
            _save_budget(buckets)
            return False, "rpm"
        if lim["rpd"] and len(req) >= lim["rpd"]:
            b["req"], b["tok"] = req, tok
            buckets[key] = b
            _save_budget(buckets)
            return False, "rpd"
        if lim["tpm"]:
            if b.get("hdr_reset", 0) > now and b.get("hdr_remaining") is not None:
                remaining = b["hdr_remaining"]  # trust the live header while fresh
            else:
                remaining = lim["tpm"] - sum(e[1] for e in tok)
            if est_tokens > remaining:
                b["req"], b["tok"] = req, tok
                buckets[key] = b
                _save_budget(buckets)
                return False, "tpm"
        if commit:
            req.append(now)
            tok.append([now, est_tokens])
        b["req"], b["tok"] = req, tok
        buckets[key] = b
        _save_budget(buckets)
        return True, "ok"


def _update_budget_from_headers(provider: str, model: str, headers: dict[str, Any]) -> None:
    """Correct the local budget from a real response's rate-limit headers."""
    rem = headers.get("x-ratelimit-remaining-tokens")
    reset = headers.get("x-ratelimit-reset-tokens")
    if rem is None and reset is None:
        return
    key = f"{provider}:{model}"
    now = time.time()
    with _lock:
        try:
            with open(BUDGET_PATH) as f:
                buckets = json.load(f)
        except Exception:  # noqa: BLE001
            buckets = {}
        b = buckets.get(key, {})
        try:
            if rem is not None:
                b["hdr_remaining"] = int(float(rem))
        except (TypeError, ValueError):
            pass
        secs = _parse_reset(reset)
        if secs is not None:
            b["hdr_reset"] = now + secs
        buckets[key] = b
        _save_budget(buckets)


def _prep_call(prov_cfg: dict[str, Any], model: str, messages: list[dict],
               mt: int) -> tuple[list[dict], int, int]:
    """Fit a call inside the provider/model TPM: cap draft INPUT to the provider's
    draft_input_cap_tokens (Groq ~3.5K, leaving output headroom) and clamp output
    max_tokens so input+output stays under TPM. Returns (messages, mt, est_total)."""
    cap_in = prov_cfg.get("draft_input_cap_tokens")
    msgs = _cap_messages(messages, cap_in) if cap_in else messages
    tpm = (prov_cfg.get("model_tpm") or {}).get(model, prov_cfg.get("tpm"))
    mt2 = mt
    if tpm:
        headroom = tpm - _est_tokens(msgs) - 256  # margin for tokenizer slack
        mt2 = max(256, min(mt, headroom)) if headroom > 256 else 256
    return msgs, mt2, _est_tokens(msgs) + mt2


def _cap_messages(messages: list[dict], cap_tokens: int) -> list[dict]:
    if _est_tokens(messages) <= cap_tokens:
        return messages
    out = [dict(m) for m in messages]
    overflow_chars = (_est_tokens(messages) - cap_tokens) * CHARS_PER_TOKEN
    for m in reversed(out):  # trim the tail of the last/largest user message
        if m.get("role") == "user" and m.get("content"):
            c = str(m["content"])
            keep = max(0, len(c) - overflow_chars)
            m["content"] = c[:keep] + "\n[...brief truncated to fit provider TPM...]"
            break
    return out


# ── the single-call primitive (the seam the smoke test stubs) ─────────────────
# Role-aware thinking/reasoning budgets (Fix 3) for the conductor's own roles. A
# CEILING, not a floor; env-overridable. synth = the planner (generous), steer =
# cheap nudge (light), escalate = frontier deliberation (generous).
_THINKING_BUDGET = {
    "synth": int(os.environ.get("CONDUCTOR_SYNTH_THINKING", "8192")),
    "steer": int(os.environ.get("CONDUCTOR_STEER_THINKING", "2048")),
    "escalate": int(os.environ.get("CONDUCTOR_ESCALATE_THINKING", "8192")),
}

# Per-rung retry on a transient 429/5xx before falling to the next rung (Fix 3) —
# a brief retry keeps the run on the $0 free rung instead of cascading to paid.
_RUNG_RETRIES = int(os.environ.get("CONDUCTOR_RUNG_RETRIES", "2"))
_RUNG_BACKOFF_S = float(os.environ.get("CONDUCTOR_RUNG_BACKOFF_S", "5"))


def _fabric_mode() -> str:
    """The active FABRIC mode name (free / free-full-local / full-local / …), read
    live from the mode file. Distinct from CONDUCTOR_MODE (the tier ceiling) — used
    so full-local can prefer the paid synth rung over the free cascade."""
    f = os.path.expanduser(os.environ.get("HERMES_MODE_FILE", "~/.hermes-max/mode"))
    try:
        with open(f) as fh:
            return fh.read().strip()
    except OSError:
        return ""


_PLAN_SIGNATURE = "## Plan authored by:"   # must be followed by "<model> via conductor"


def _ranked_repo_map(cwd: str, task: str) -> str:
    """Mention-seeded, PageRank-ranked repo map (tools/repomap.py) — files the task mentions
    rank first, fitted to a token budget. Best-effort: "" on any failure → caller falls back
    to the scopemap structural map."""
    try:
        import os as _os
        repo = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        if repo not in sys.path:
            sys.path.insert(0, repo)
        from tools import repomap
        m = repomap.build_repomap(cwd, query=task, max_tokens=2500)
        return m if "### " in m else ""  # require at least one ranked file
    except Exception:  # noqa: BLE001
        return ""


def _scopemap_repo_map(cwd: str) -> str:
    """Best-effort structural map via the scopemap core (added to path lazily)."""
    try:
        import os as _os
        repo = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        sm = _os.path.join(repo, "mcp-scopemap")
        if sm not in sys.path:
            sys.path.insert(0, sm)
        import scopemap_core
        return scopemap_core.get_repo_map(cwd)
    except Exception:  # noqa: BLE001
        return ""


# ── PLANNER_PROMPT_SPEC §4 — frontier classifier (keyword CLASSES, never a problem
# allowlist). Any class firing, OR a structural >3-file signal, routes to the frontier
# planner: embedded invariants become mandatory and the synth budget is raised. The
# example domains below are signal classes, not a hardcoded problem set.
_FRONTIER_SIGNAL_CLASSES: dict[str, tuple[str, ...]] = {
    "concurrency": ("lock-free", "lockfree", "wait-free", "atomic", "compare-and-swap", "cas ",
                    "mutex", "semaphore", "data race", "memory model", "ordering", "mpmc", "spsc",
                    "mpsc", "ring buffer", "concurren", "lock contention"),
    "systems": ("allocator", "scheduler", "syscall", "mmap", "zero-copy", "simd", "page table",
                "garbage collect", "kernel", "bytecode", "interpreter", "vm "),
    "algorithmic_novelty": ("from scratch", "b-tree", "lsm tree", "skip list", "union-find",
                            "novel algorithm", "parser", "compiler", "state machine"),
    "crypto_numeric": ("crypto", "cipher", "merkle", "elliptic", "zk-snark", "snark", "zero-knowledge",
                       "fixed-point", "numerical stability", "floating-point error"),
    "distributed_protocol": ("raft", "paxos", "consensus", "protocol", "replication", "two-phase commit",
                             "2pc", "gossip", "vector clock", "rpc framing", "codec"),
}


def classify_task(task: str, n_files: int = 0) -> dict[str, Any]:
    """§4 classifier → {frontier, classes, structural, signals}. Frontier when any signal
    class fires or the task spans >3 files; lightweight otherwise. Ambiguity is resolved
    toward frontier elsewhere (conductor tokens are cheap; an executor spiral is not)."""
    t = (task or "").lower()
    fired: dict[str, list[str]] = {}
    for cls, sigs in _FRONTIER_SIGNAL_CLASSES.items():
        hit = [s.strip() for s in sigs if s in t]
        if hit:
            fired[cls] = hit
    structural = n_files > 3
    return {"frontier": bool(fired) or structural, "classes": list(fired),
            "structural": structural, "signals": [s for h in fired.values() for s in h][:6]}


def _is_frontier_task(task: str, n_files: int = 0) -> bool:
    return classify_task(task, n_files)["frontier"]


# ── PLANNER_PROMPT_SPEC §1 — the plan schema (section TYPES, never problem types) ─────
PLAN_SCHEMA_SECTIONS: tuple[str, ...] = (
    "CONTEXT", "ARCHITECTURE DECISIONS", "STEPS", "VERIFICATION", "REFERENCES")

# §7 anti-patterns AP1–AP7: phrases that betray an un-committed plan. Banned ANYWHERE
# except inside a 'BECAUSE …' justification clause (where naming the rejected alternative
# is legitimate). The planner commits to one choice; it never hands the executor a menu of
# options to resolve.
_PLAN_BANNED = (
    (re.compile(r"\bconsider\b", re.I), "AP1 'consider' — commit to a choice, don't offer one"),
    (re.compile(r"\byou could\b", re.I), "AP2 'you could' — commit, don't suggest"),
    (re.compile(r"\bdepending on\b", re.I), "AP3 'depending on' — resolve the condition now, don't defer"),
    (re.compile(r"\beither\b", re.I), "AP4 'either' — pick one, don't leave a fork"),
    (re.compile(r"\btests?\s+pass\b", re.I), "AP5 'tests pass' — give the exact command + exit code"),
    (re.compile(r"\bworks?\s+correctly\b", re.I), "AP6 'works correctly' — give a mechanical check"),
)


def _plan_system(frontier: bool) -> str:
    """PLANNER_PROMPT_SPEC §2 — the load-bearing rules (no rationale/commentary)."""
    s = (
        "You are the PLANNER. You author PLAN.md — a CONTRACT a small local executor "
        "TRANSCRIBES literally. The executor cannot design; YOU make every decision, here, now.\n\n"
        "Output ONLY the markdown plan (no preamble, no code fences). It MUST contain these "
        "sections, in THIS order, with these EXACT ## headings:\n\n"
        "## CONTEXT\nOne paragraph: the goal and the binding constraints.\n\n"
        "## ARCHITECTURE DECISIONS\nA numbered list. For EVERY non-trivial choice the task "
        "implies (data structure, algorithm, concurrency/atomicity mechanism, error model, I/O "
        "strategy), COMMIT to ONE specific named mechanism — each ending with 'BECAUSE <why this "
        "over the main alternative>'. DECISIONS, not options.\n\n"
        "## STEPS\nAn ordered, ATOMIC list. EACH step is exactly:\n"
        "  - DO: one concrete action naming the exact file + function/signature.\n"
        "  - DONE-WHEN: an exact command + expected exit code/output (a mechanical binary check).\n"
        "  - LIKELY-FAILURE: the specific way this step tends to fail; PREEMPT: how to avoid it.\n"
        "Mark a hard step 'complexity: HIGH'. Multi-file: append 'files: a.py,b.py' and "
        "'depends_on: [1,2]' to the DO line.\n\n"
        "## VERIFICATION\nThe exact commands that prove the WHOLE task done (lint + types + tests) "
        "and their expected results.\n\n"
        "## REFERENCES\nThe exact algorithm/paper/known implementation each decision follows — or "
        "'none'. Reference NOTHING the executor has not been shown (no unseen files/APIs).\n\n"
        "RULES (non-negotiable):\n"
        "- COMMIT, do not offer. Never 'consider', 'you could', 'depending on', 'either', or an "
        "unresolved 'A or B'. Pick one; justify with BECAUSE (only there may you name the rejected "
        "alternative).\n"
        "- DONE-WHEN is MECHANICAL. Never 'tests pass' / 'works correctly' — give the exact command "
        "and exact expected exit code/output.\n"
        "- ANTICIPATE the executor's failure: every step states LIKELY-FAILURE + PREEMPT.\n"
        "- Steps are ATOMIC and ORDERED. PIN everything: exact signatures, versions, paths.\n")
    if frontier:
        s += ("\nFRONTIER TASK: the executor CANNOT design this. Commit every atomicity/ordering/"
              "algorithmic INVARIANT with the precise named mechanism and a concrete reference to "
              "follow verbatim; spell out the invariant each step must preserve. A deferred design "
              "choice is a FAILED plan.\n")
    return s


def _plan_user(task: str, ctx: str, retry_context: str = "", research_context: str = "") -> str:
    """PLANNER_PROMPT_SPEC §3 — XML-tagged user template. Long, stable context FIRST;
    the instruction LAST (placement rationale: the model attends most to the tail)."""
    return (
        f"<repo_context>\n{ctx}\n</repo_context>\n\n"
        f"<research_context>\n{research_context or 'none'}\n</research_context>\n\n"
        f"<retry_context>\n{retry_context or 'none — this is the first plan'}\n</retry_context>\n\n"
        f"<task>\n{task}\n</task>\n\n"
        "<instruction>\nProduce PLAN.md for <task>, following the schema and rules EXACTLY. "
        "Commit every decision; make every DONE-WHEN a mechanical check. Output only the plan.\n"
        "</instruction>")


def lint_plan(plan_md: str) -> list[str]:
    """§6 determinism enforcer — the reason compliance does not depend on which model rung
    answered. Returns a list of violations (empty = a clean, transcribable plan):
      • a required §1 section is missing;
      • a STEP lacks a mechanical 'DONE-WHEN:' line;
      • a §7 banned phrase appears outside a BECAUSE clause (AP1–AP6);
      • an unresolved 'A or B' choice appears in a decision/step line (AP7)."""
    plan_md = plan_md or ""
    violations: list[str] = []

    for sec in PLAN_SCHEMA_SECTIONS:
        if not re.search(rf"(?im)^\s*#+\s*{re.escape(sec)}\b", plan_md):
            violations.append(f"missing required section: ## {sec}")

    # STEPS: every 'DO:' must be matched by a 'DONE-WHEN:' (each step has both).
    steps_block = ""
    m = re.search(r"(?is)^#+\s*STEPS\b(.*?)(?:^#+\s|\Z)", plan_md, re.M)
    if m:
        steps_block = m.group(1)
    n_do = len(re.findall(r"(?im)^\s*[-*\d.)\s]*DO:", steps_block))
    n_done = len(re.findall(r"(?i)DONE[\s\-]?WHEN:", steps_block))
    if n_done == 0:
        violations.append("STEPS has no mechanical 'DONE-WHEN:' check")
    elif n_do and n_done < n_do:
        violations.append(f"{n_do - n_done} step(s) missing a 'DONE-WHEN:' line")

    # Banned phrases / unresolved forks — line by line, skipping BECAUSE justifications.
    for line in plan_md.splitlines():
        if "BECAUSE" in line.upper():
            continue
        low = line.strip()
        if not low or low.startswith(("#", ">", "```")):
            continue
        for rx, msg in _PLAN_BANNED:
            if rx.search(line):
                violations.append(f"{msg}: “{low[:70]}”")
        # AP7: an unresolved 'X or Y' offered as a choice (not inside a justification).
        if re.search(r"\b\w+\s+or\s+\w+\b", line, re.I) and re.search(r"(?i)\b(use|choose|pick|option|approach|either|maybe)\b", line):
            violations.append(f"AP7 unresolved 'or' fork — commit to one: “{low[:70]}”")
    return violations


def _load_skills_md(cwd: str) -> str:
    """Project-level agent context (SKILLS.md at the repo root): locked design decisions,
    protected files, and test commands. Injected into the planning context so the plan
    respects what the project has already settled instead of relitigating it each session."""
    import os as _os
    p = _os.path.join(cwd, "SKILLS.md")
    try:
        if _os.path.isfile(p):
            return open(p, encoding="utf-8", errors="replace").read()[:1500]
    except OSError:
        pass
    return ""


def _archive_existing_plan(cwd: str) -> str | None:
    """Before the conductor OVERWRITES PLAN.md, preserve the prior plan to
    plans/PLAN_NNN.md (auto-incrementing) so the plan history isn't lost. Best-effort —
    a failure here never blocks writing the new plan."""
    import os as _os
    plan_path = _os.path.join(cwd, "PLAN.md")
    if not _os.path.isfile(plan_path):
        return None
    try:
        plans_dir = _os.path.join(cwd, "plans")
        _os.makedirs(plans_dir, exist_ok=True)
        n = sum(1 for f in _os.listdir(plans_dir)
                if f.startswith("PLAN_") and f.endswith(".md")) + 1
        dest = _os.path.join(plans_dir, f"PLAN_{n:03d}.md")
        with open(plan_path, encoding="utf-8", errors="replace") as src:
            prior = src.read()
        with open(dest, "w", encoding="utf-8") as dst:
            dst.write(prior)
        return dest
    except OSError:
        return None


def _conventions():
    """Import the conventions module from mcp-knowledge-graph (added to path lazily). None
    if unavailable — callers degrade silently (no KG, no decision memory, no crash)."""
    try:
        import os as _os
        kg = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                           "mcp-knowledge-graph")
        if kg not in sys.path:
            sys.path.insert(0, kg)
        import conventions
        return conventions
    except Exception:  # noqa: BLE001
        return None


def _save_plan_decisions(plan_md: str, cwd: str) -> int:
    """Extract '## ARCHITECTURE DECISIONS' items (each a committed 'X BECAUSE …' line) from a
    plan and upsert them to convention memory (category='decision', scope=cwd basename) — the
    *why* behind each decision, queryable in a later plan. Idempotent by content hash; silent
    on KG absence. Returns the count newly saved."""
    cv = _conventions()
    if cv is None:
        return 0
    m = re.search(r"##\s*ARCHITECTURE DECISIONS\s*\n(.*?)(?=\n##|\Z)", plan_md or "",
                  re.DOTALL | re.IGNORECASE)
    if not m:
        return 0
    import os as _os
    scope = _os.path.basename(cwd.rstrip("/")) if cwd else "global"
    n = 0
    for line in m.group(1).splitlines():
        line = line.strip().lstrip("0123456789.-) *").strip()
        if len(line) < 20 or "because" not in line.lower():
            continue
        try:
            if cv.save_convention(category="decision", data=line,
                                  tags=["auto", "plan"], scope=scope).get("saved"):
                n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


def _past_decisions(cwd: str) -> str:
    """Past committed decisions for this repo, formatted for injection into the planner
    context so the conductor reuses (not re-derives) them and sees what a pivot already
    rejected. "" when none / KG down. Capped ~600 chars."""
    cv = _conventions()
    if cv is None or not cwd:
        return ""
    try:
        import os as _os
        decisions = cv.get_conventions(category="decision", scope=_os.path.basename(cwd.rstrip("/")))
    except Exception:  # noqa: BLE001
        return ""
    if not decisions:
        return ""
    lines = ["## Past decisions (do not revisit these)"]
    for d in decisions[:8]:
        row = f"- {d.get('data', '')[:120]}"
        if "pivot" in (d.get("tags") or []):
            row += "  [PIVOT — prior approach failed]"
        lines.append(row)
    return "\n".join(lines)[:600]


def conductor_plan(task: str, cwd: str = "", repo_map: str = "") -> dict[str, Any]:
    """THE planner entrypoint (the guaranteed first step on any new task). The CONDUCTOR
    authors PLAN.md — not the executor's internal chain-of-thought. It maps the repo
    (scopemap), routes the plan through the synth chain (kimi-k2.6:free → V4-Pro on 429)
    with the full 8192-token thinking budget, and writes a SIGNED PLAN.md to `cwd`:

        ## Plan authored by: <model> via conductor

    The verify gate refuses any PLAN.md without that signature, so a plan the executor
    wrote itself can never pass — the architectural thinking is done by the strong
    cloud reasoner, the local model only executes against the contract.

    Idempotent: if a validly-signed PLAN.md already exists in `cwd`, it is returned
    unchanged. Returns {ok, plan, model, provider, signed, wrote, path}."""
    import os as _os
    cwd = _os.path.abspath(_os.path.expanduser(cwd or _os.getcwd()))
    plan_path = _os.path.join(cwd, "PLAN.md")
    reset_escalation_budget()   # new task = new run: zero the mid-loop escalation counters
    # idempotent: a validly-signed plan already present → return it
    try:
        if _os.path.isfile(plan_path):
            existing = open(plan_path).read()
            if _PLAN_SIGNATURE in existing and "via conductor" in existing:
                return {"ok": True, "plan": existing, "signed": True,
                        "wrote": False, "path": plan_path,
                        "model": "(existing)", "provider": "(existing)"}
    except OSError:
        pass

    if not repo_map and cwd:
        # Prefer the mention-seeded ranked map (task-relevant files first); fall back to the
        # scopemap structural map when tools/repomap is unavailable or finds nothing.
        repo_map = _ranked_repo_map(cwd, task) or _scopemap_repo_map(cwd)
    greenfield = (not repo_map) or "greenfield" in repo_map.lower()
    ctx = ("GREENFIELD — no existing code to map." if greenfield
           else f"Repository structure (one line per file):\n{repo_map[:8000]}")
    skills = _load_skills_md(cwd) if cwd else ""
    if skills:
        ctx += ("\n\nPROJECT SKILLS.md (respect these — locked decisions, protected files, "
                "test commands; do NOT modify protected files or reopen locked decisions):\n"
                + skills)
    past = _past_decisions(cwd)  # prior committed decisions (+ what a pivot rejected)
    if past:
        ctx += "\n\n" + past
    frontier = _is_frontier_task(task)
    system = _plan_system(frontier)
    user = _plan_user(task, ctx)
    # synth chain carries thinking_budget 8192; frontier specs need more output room.
    _mt = 6144 if frontier else 4096

    def _gen(extra_user: str = "") -> dict[str, Any]:
        return run_role("synth", messages=[{"role": "system", "content": system},
                                           {"role": "user", "content": user + extra_user}],
                        max_tokens=_mt)

    res = _gen()
    if not (res.get("ok") and res.get("content")):
        return {"ok": False, "plan": "", "signed": False, "wrote": False,
                "path": plan_path, "reason": res.get("reason", "no synth rung available")}
    body = res["content"].strip()

    # ── §6 determinism gate: lint, and on violations re-generate ONCE with the exact
    # violations appended as a hard correction. Keep whichever plan lints cleaner. A
    # plan that still trips the linter is shipped with the residual surfaced (warn),
    # never silently — the executor's verify gate is the final backstop.
    violations = lint_plan(body)
    if violations:
        _otel("plan_lint_retry", {"violations": len(violations)})
        correction = ("\n\n<correction>\nYour previous plan violated the contract. FIX ALL of "
                      "these and re-output the COMPLETE plan (every section), nothing else:\n"
                      + "\n".join(f"- {v}" for v in violations[:12]) + "\n</correction>")
        res2 = _gen(correction)
        if res2.get("ok") and res2.get("content"):
            body2 = res2["content"].strip()
            if len(lint_plan(body2)) < len(violations):
                res, body, violations = res2, body2, lint_plan(body2)
    model = res.get("model", "unknown")
    # strip any header the model emitted, then prepend the canonical signature
    signed = f"## Plan authored by: {model} via conductor\n\n{body}\n"
    _archive_existing_plan(cwd)  # preserve the prior plan to plans/PLAN_NNN.md before overwrite
    try:
        with open(plan_path, "w") as f:
            f.write(signed)
    except OSError as e:
        return {"ok": False, "plan": signed, "signed": True, "wrote": False,
                "path": plan_path, "reason": f"write failed: {e}"}
    # Capture the committed ARCHITECTURE DECISIONS (the 'why') into convention memory so a
    # later plan reuses them instead of re-deriving. Best-effort; silent on KG absence.
    _save_plan_decisions(signed, cwd)
    return {"ok": True, "plan": signed, "signed": True, "wrote": True, "path": plan_path,
            "model": model, "provider": res.get("provider"),
            "thinking_tok": res.get("thinking_tok", 0),
            "lint_violations": violations, "frontier": frontier}


# ── PLANNER_PROMPT_SPEC §5 — mid-run escalation / replanning callback ──────────────────
_ESCALATION_DECISIONS = ("patch-step", "pivot-approach", "abort-and-resummarize")

_ESCALATE_SYSTEM = (
    "You are the CONDUCTOR handling a mid-run failure. A small executor is following a PLAN.md "
    "you authored and a step has FAILED its DONE-WHEN. Diagnose the ROOT CAUSE and choose the "
    "MINIMAL intervention — touch only the failing step or the single decision that was wrong; "
    "do NOT rewrite the plan.\n\n"
    "Output EXACTLY three lines, nothing else:\n"
    "DIAGNOSIS: <one sentence — the root cause>\n"
    "DECISION: <exactly one of: patch-step | pivot-approach | abort-and-resummarize>\n"
    "PATCH: <the minimal change — the corrected step text, or the one replacement decision, or "
    "(for abort-and-resummarize) a 2-line STUCK SUMMARY of what is verifiably true and what failed>\n\n"
    "patch-step = the step is salvageable with a concrete fix. pivot-approach = the decision "
    "behind the step was wrong; give the replacement. abort-and-resummarize = the context is "
    "polluted or the approach is dead; summarize for a clean restart.")


def _parse_escalation(text: str) -> dict[str, str] | None:
    """Strict parse of the §5 three-field output. None if it does not match (caller re-asks)."""
    if not text:
        return None
    d = re.search(r"(?im)^\s*DIAGNOSIS:\s*(.+?)\s*$", text)
    dec = re.search(r"(?im)^\s*DECISION:\s*([a-z][a-z\-]+)", text)
    p = re.search(r"(?ism)^\s*PATCH:\s*(.+)$", text)
    if not (d and dec and p):
        return None
    decision = dec.group(1).strip().lower()
    if decision not in _ESCALATION_DECISIONS:
        return None
    return {"diagnosis": d.group(1).strip(), "decision": decision, "patch": p.group(1).strip()}


def conductor_escalate(plan_md: str, failing_step: str, error_output: str,
                       completed_steps: list[str] | None = None, cwd: str = "") -> dict[str, Any]:
    """Mid-run replanning: when a step repeatedly fails its DONE-WHEN (or a tool errors past
    the consecutive-failure threshold), the executor calls this INSTEAD of blind retry. The
    conductor returns a strict {diagnosis, decision ∈ patch-step|pivot-approach|
    abort-and-resummarize, patch}. Strict-parsed; re-asked once; falls back to
    abort-and-resummarize. Never raises."""
    completed = "; ".join(completed_steps or []) or "none"
    user = (f"<plan>\n{(plan_md or '')[:6000]}\n</plan>\n\n"
            f"<failing_step>\n{failing_step}\n</failing_step>\n\n"
            f"<error_output>\n{(error_output or '')[:3000]}\n</error_output>\n\n"
            f"<completed_steps>\n{completed}\n</completed_steps>\n\n"
            "<instruction>Diagnose and decide. Output ONLY the three DIAGNOSIS/DECISION/PATCH "
            "lines.</instruction>")
    extra = ""
    for _ in range(2):
        res = run_role("synth", messages=[{"role": "system", "content": _ESCALATE_SYSTEM},
                                          {"role": "user", "content": user + extra}], max_tokens=2048)
        if not (res.get("ok") and res.get("content")):
            return {"ok": False, "reason": res.get("reason", "no synth rung available"),
                    "decision": "abort-and-resummarize", "diagnosis": "", "patch": ""}
        parsed = _parse_escalation(res["content"])
        if parsed:
            _otel("conductor_escalate", {"decision": parsed["decision"]})
            # A pivot-approach PATCH is a NEW architecture decision that supersedes a failed
            # one — record it (tagged 'pivot') so future plans see what was tried and rejected.
            if parsed["decision"] == "pivot-approach" and "because" in (parsed.get("patch", "")).lower():
                cv = _conventions()
                if cv is not None:
                    import os as _os
                    try:
                        cv.save_convention(category="decision", data=parsed["patch"].strip(),
                                           tags=["auto", "pivot", "escalation"],
                                           scope=_os.path.basename(cwd.rstrip("/")) if cwd else "global")
                    except Exception:  # noqa: BLE001 - never block escalation on a KG write
                        pass
            return {"ok": True, "model": res.get("model", "unknown"), **parsed}
        extra = ("\n\n<correction>Your response did not match the required format. Output EXACTLY "
                 "three lines: 'DIAGNOSIS: …', then 'DECISION: <patch-step|pivot-approach|"
                 "abort-and-resummarize>', then 'PATCH: …'. Nothing else.</correction>")
    _otel("conductor_escalate", {"decision": "abort-and-resummarize", "parse": "failed"})
    return {"ok": True, "model": "unknown", "diagnosis": "(unparseable conductor response)",
            "decision": "abort-and-resummarize", "patch": ""}


# ── escalation economic guard (per-run call budget) ───────────────────────────
def _esc_budget_path() -> str:
    d = os.path.expanduser(os.environ.get("HERMES_MAX_STATE_DIR", "~/.hermes-max")) + "/conductor"
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "escalation_budget.json")


def _esc_caps() -> dict[str, int]:
    return {"standard": int(os.environ.get("CONDUCTOR_MAX_ESC_STANDARD", "5")),
            "deep": int(os.environ.get("CONDUCTOR_MAX_ESC_DEEP", "2"))}


def reset_escalation_budget() -> None:
    """Start-of-run reset (called by conductor_plan): zero the per-run call counters."""
    try:
        with open(_esc_budget_path(), "w") as f:
            json.dump({"standard": 0, "deep": 0, "paid": 0, "cost_usd": 0.0}, f)
    except OSError:
        pass


def _esc_counts() -> dict[str, Any]:
    try:
        with open(_esc_budget_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"standard": 0, "deep": 0, "paid": 0, "cost_usd": 0.0}


def _esc_account(budget: str, paid: bool, cost: float) -> dict[str, Any]:
    c = _esc_counts()
    c[budget] = int(c.get(budget, 0)) + 1
    if paid:
        c["paid"] = int(c.get("paid", 0)) + 1
        c["cost_usd"] = round(float(c.get("cost_usd", 0.0)) + cost, 6)
    try:
        with open(_esc_budget_path(), "w") as f:
            json.dump(c, f)
    except OSError:
        pass
    return c


def escalation_summary() -> dict[str, Any]:
    """End-of-run summary: total escalations, free vs paid, $ spent."""
    c = _esc_counts()
    total = int(c.get("standard", 0)) + int(c.get("deep", 0))
    paid = int(c.get("paid", 0))
    return {"calls": total, "free": total - paid, "paid": paid,
            "cost_usd": round(float(c.get("cost_usd", 0.0)), 6)}


_TRIGGER_TOOL = {
    "self_declared": "uplift·ask", "verify_double_fail": "uplift·stuck",
    "complex_step": "uplift·step",
}


def review_and_adapt(issue: str, current_step: int, completed_steps: list | None = None,
                     context: str = "", cwd: str = "", budget: str = "standard") -> dict[str, Any]:
    """The living plan (Fix 4): when the executor discovers a plan step is impossible as
    written, the conductor REVISES PLAN.md from `current_step` onward — completed steps
    are preserved verbatim, so prior work isn't thrown away. Counts against the deep
    escalation budget (max 2 paid/run). Logs `plan.adapt step N <model> <tok>`. Returns
    {ok, revised, model, provider, path}."""
    import os as _os
    completed_steps = completed_steps or []
    # budget guard (shared with deep escalations)
    caps = _esc_caps()
    if int(_esc_counts().get("deep", 0)) >= caps.get("deep", 2):
        _emit_livelog("plan·adapt", ok=False, reason="deep budget exhausted — proceed with current plan")
        return {"ok": False, "refused": True, "reason": f"deep budget ({caps['deep']}) reached this run"}

    done = "\n".join(f"- [x] step {i + 1}: {s}" for i, s in enumerate(completed_steps)) or "(none)"
    prompt = (
        f"The executor hit an issue at step {current_step} of the plan.\n\n"
        f"Issue: {issue}\n\n"
        f"Completed steps (preserve these EXACTLY — do not redo them):\n{done}\n\n"
        f"Context:\n{(context or '')[:6000]}\n\n"
        f"Revise the plan FROM step {current_step} onward to address this issue. Output the "
        "revised steps as a markdown '## Steps' list (use real, verified APIs). Keep the "
        "DONE_CONDITION intact unless the issue requires changing it. Mark "
        "'complexity: HIGH' on any revised step that needs frontier reasoning.")
    t0 = time.time()
    r = run_role("synth", prompt=prompt, max_tokens=2048 if budget != "deep" else 4096)
    secs = time.time() - t0
    if not (r.get("ok") and r.get("content")):
        _emit_livelog("plan·adapt", ok=False, reason=str(r.get("reason", "no rung"))[:80], secs=secs)
        return {"ok": False, "reason": r.get("reason", "synth unavailable")}
    prov = r.get("provider")
    tier = (reg.load_config()["providers"].get(prov, {}) or {}).get("tier", "?") if prov else "?"
    _esc_account("deep", paid=(tier != "free"), cost=float(r.get("cost_usd", 0.0) or 0.0))
    # Rewrite PLAN.md: keep the conductor signature + the completed steps verbatim, append
    # the revision. Staying signed means verify still treats it as conductor-authored.
    cwd = _os.path.abspath(_os.path.expanduser(cwd or _os.getcwd()))
    plan_path = _os.path.join(cwd, "PLAN.md")
    ts = time.strftime("%H:%M:%S", time.localtime())
    try:
        old = open(plan_path).read() if _os.path.isfile(plan_path) else ""
    except OSError:
        old = ""
    sig = old.splitlines()[0] if old.startswith("## Plan authored by:") else \
        f"## Plan authored by: {r.get('model')} via conductor"
    revised = (f"{sig} (adapted at step {current_step} by {r.get('model')} via conductor, {ts})\n\n"
               f"{old.split(chr(10), 1)[1] if (chr(10) in old) else ''}\n"
               f"## Plan adaptation — revised from step {current_step} ({ts})\n"
               f"Issue: {issue}\n\n"
               f"Completed (preserved):\n{done}\n\n{r['content'].strip()}\n")
    try:
        with open(plan_path, "w") as f:
            f.write(revised)
    except OSError as e:
        return {"ok": False, "reason": f"write failed: {e}", "revised": r["content"]}
    out_tok = int((r.get("usage") or {}).get("completion_tokens", 0) or 0)
    _emit_livelog("plan·adapt", ok=True, secs=secs,
                  ret={"step": current_step, "model": r.get("model"), "tier": tier, "tokens": out_tok})
    return {"ok": True, "revised": r["content"], "model": r.get("model"), "provider": prov,
            "tier": tier, "tokens": out_tok, "path": plan_path}


def reasoning_escalation(question: str, context: str = "", budget: str = "standard",
                         trigger: str = "self_declared") -> dict[str, Any]:
    """A targeted second opinion from a larger reasoning model (mid-loop frontier uplift).
    The executor's escape hatch from its CoT budget: instead of spinning on a hard
    architectural/algorithmic question, ask it directly and act on a precise answer.

    Triggers: self_declared (executor unsure) | verify_double_fail (stuck, 2× fail) |
              complex_step (a plan step marked complexity:HIGH).
      budget=standard → fast, $0 (free synth cascade, modest cap)
      budget=deep     → thorough (free cascade → V4-Pro paid fallback, larger cap)

    Economic guard: capped per run (CONDUCTOR_MAX_ESC_STANDARD=5, _DEEP=2). Past the
    deep cap, further deep asks are logged and refused (the executor proceeds with what
    it has) so one pathological run can't burn the credit. The answer is returned as a
    structured '## Frontier guidance' block ready to inject into the executor's context.
    Never raises."""
    caps = _esc_caps()
    counts = _esc_counts()
    tool = _TRIGGER_TOOL.get(trigger, "uplift·deep")
    if int(counts.get(budget, 0)) >= caps.get(budget, 99):
        _emit_livelog(tool, ok=False,
                      reason=f"{budget} escalation budget exhausted ({caps[budget]}) — proceed with current context")
        return {"ok": False, "refused": True, "budget": budget, "trigger": trigger,
                "answer": "", "reason": f"{budget} escalation cap ({caps[budget]}) reached this run"}

    mt = 1024 if budget != "deep" else 4096
    prompt = ("You are a senior engineer giving a targeted second opinion. Answer "
              "concisely and concretely (no preamble).\n\n"
              f"QUESTION:\n{question}\n\nRELEVANT CONTEXT:\n{(context or '')[:8000]}")
    t0 = time.time()
    r = run_role("synth", prompt=prompt, max_tokens=mt)
    secs = time.time() - t0
    prov = r.get("provider")
    tier = (reg.load_config()["providers"].get(prov, {}) or {}).get("tier", "?") if prov else "?"
    out_tok = int((r.get("usage") or {}).get("completion_tokens", 0) or 0)
    cost = float(r.get("cost_usd", 0.0) or 0.0)
    ans = r.get("content") or r.get("reason") or ""
    if r.get("ok"):
        _esc_account(budget, paid=(tier != "free"), cost=cost)
        _emit_livelog(tool, ok=True, secs=secs,
                      ret={"q": question[:50], "model": r.get("model"), "tier": tier,
                           "tokens": out_tok, "thinking_tok": r.get("thinking_tok", 0)})
    else:
        _emit_livelog(tool, ok=False, reason=str(ans)[:80], secs=secs)
    # structured guidance block — ready to PREPEND to the executor's next prompt
    ts = time.strftime("%H:%M:%S", time.localtime())
    guidance = (f"## Frontier guidance (from {r.get('model','?')}, {ts})\n"
                f"Question: {question}\nAnswer: {ans}\n---\n")
    return {
        "ok": bool(r.get("ok")), "trigger": trigger, "budget": budget,
        "answer": ans, "guidance": guidance,
        "model": r.get("model"), "provider": prov, "tier": tier,
        "tokens": out_tok, "thinking_tok": int(r.get("thinking_tok", 0) or 0),
        "cost_usd": cost, "run_escalations": _esc_counts(),
    }


def _emit_livelog(tool: str, ok: bool, ret: dict | None = None,
                  reason: str | None = None, secs: float | None = None) -> None:
    """Best-effort livelog emit (repo root on path lazily). Never raises."""
    try:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if repo not in sys.path:
            sys.path.insert(0, repo)
        from lib import livelog
        if ok:
            livelog.tool_ok(tool, secs=secs, ret=ret)
        else:
            livelog.tool_fail(tool, reason=reason, secs=secs)
    except Exception:  # noqa: BLE001
        pass


def _reasoning_body(base_url: str, budget: int) -> dict[str, Any] | None:
    """A provider-appropriate reasoning param, sent ONLY where it's known-safe so an
    unknown field never 400s a provider. OpenRouter accepts `reasoning.max_tokens`."""
    if budget <= 0:
        return None
    if "openrouter" in (base_url or ""):
        return {"reasoning": {"max_tokens": budget}}
    return None


def _post_chat(base_url: str, api_key: str, model: str, messages: list[dict],
               max_tokens: int, extra_body: dict[str, Any] | None = None
               ) -> tuple[dict[str, Any], dict[str, str]]:
    """Returns (json_body, response_headers). Headers feed the live TPM budget.
    `extra_body` carries the thinking/reasoning budget (Fix 3)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if extra_body:
        payload.update(extra_body)
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(f"{base_url.rstrip('/')}/chat/completions",
                           json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json(), {k.lower(): v for k, v in resp.headers.items()}


def _price(prov_cfg: dict[str, Any], role: str) -> dict[str, float]:
    p = prov_cfg.get("price", {})
    return p.get(role) or p.get("synth") or p.get("steer") or {"in": 0.0, "out": 0.0}


def _is_free(prov_cfg: dict[str, Any], role: str) -> bool:
    pr = _price(prov_cfg, role)
    return (pr.get("in", 0.0) + pr.get("out", 0.0)) <= 0.0


def _cost(prov_cfg: dict[str, Any], role: str, usage: dict[str, Any]) -> float:
    pr = _price(prov_cfg, role)
    return (usage.get("prompt_tokens", 0) / 1e6 * pr.get("in", 0.0)
            + usage.get("completion_tokens", 0) / 1e6 * pr.get("out", 0.0))


def _model_for(prov_cfg: dict[str, Any], provider: str, role: str) -> str:
    models = prov_cfg.get("models", {})
    # env single-model overrides (optional) take precedence for steer/synth/escalate
    env_override = {
        "steer": os.environ.get("CONDUCTOR_STEER_MODEL"),
        "synth": os.environ.get("CONDUCTOR_SYNTH_MODEL"),
        "escalate": os.environ.get("CONDUCTOR_ESCALATE_MODEL"),
    }.get(role)
    if env_override and provider == "deepinfra":  # env model strings are DeepInfra-shaped
        return env_override
    return models.get(role) or models.get("synth") or models.get("steer") or ""


# ── ORDERED ROLE EXECUTOR ─────────────────────────────────────────────────────
def run_role(role: str, messages: list[dict] | None = None, *, prompt: str | None = None,
             max_tokens: int | None = None) -> dict[str, Any]:
    """Execute an ordered role (steer/synth/escalate) over its present chain.

    Returns a dict ALWAYS (never raises). On success: {ok:True, provider, model,
    content, usage, cost_usd, fell:[...]}. If the role is OFF (no present rung) or
    every present rung failed/was-capped: {ok:False, proceed_local:True, ...}."""
    role = (role or "").strip().lower()
    if messages is None:
        messages = [{"role": "user", "content": prompt or ""}]
    cfg = reg.load_config()
    providers = cfg["providers"]
    caps = cfg["caps"]
    chain = cfg["role_chains"].get(role, [])
    # The synth (planner) chain is reshaped by the FABRIC mode (free = full cascade;
    # free-full-local = kimi:free → V4-Pro only; full-local = V4-Pro paid first).
    if role == "synth":
        chain = reg.synth_chain_for_mode(chain, providers, _fabric_mode())
    env = dict(os.environ)
    present = resolver.resolve_chain(chain, providers, env)
    if not present:
        return {"ok": False, "proceed_local": True, "role": role, "role_active": False,
                "reason": f"role '{role}' is OFF (no present provider key in its chain) "
                          "-> the driver proceeds local-only", "attempts": []}

    mt = max_tokens or MAX_TOKENS
    attempts: list[dict[str, Any]] = []
    for pid in present:
        prov = providers[pid]
        free = _is_free(prov, role)
        if not free:
            lg = _load_ledger()
            blocked = _cap_blocked(caps, lg)
            if blocked:
                attempts.append({"provider": pid, "skipped": "usd_cap", "why": blocked})
                _trace("rung_fell", role=role, frm=pid, to="(next)", reason=blocked)
                continue
        model = _model_for(prov, pid, role)
        key = env.get(prov.get("env_key_name", ""), "")
        # PRE-FLIGHT TPM/RPM/RPD: fit the brief, then skip (not 429) if over budget.
        msgs, mt_eff, est = _prep_call(prov, model, messages, mt)
        ok_b, why = _budget_check(pid, model, prov, est, commit=True)
        if not ok_b:
            attempts.append({"provider": pid, "skipped": f"{why}_exhausted"})
            _trace("rung_fell", role=role, frm=pid, model=model, to="(next)",
                   reason=f"{why} budget exhausted")
            continue
        budget = _THINKING_BUDGET.get(role, 0)
        extra_body = _reasoning_body(prov.get("base_url", ""), budget)
        # Two attempts per rung with a short backoff before falling through — a free-tier
        # 429 is often transient, so a brief retry keeps the run on the $0 rung instead of
        # cascading straight to paid (Fix 3). Non-429 errors fall through immediately.
        data = hdrs = None
        reason = ""
        for _attempt in range(_RUNG_RETRIES):
            try:
                data, hdrs = _post_chat(prov["base_url"], key, model, msgs, mt_eff, extra_body)
                _update_budget_from_headers(pid, model, hdrs)
                content = data.get("choices", [{}])[0].get("message", {}).get("content")
                usage = data.get("usage", {}) or {}
                if content is None:  # reasoning models can return empty content if budget burned
                    raise ValueError("empty content (reasoning budget exhausted?)")
                reason = ""
                break
            except Exception as e:  # noqa: BLE001
                reason = f"{type(e).__name__}: {str(e)[:80]}"
                status = getattr(getattr(e, "response", None), "status_code", 0) or 0
                transient = status in (429, 500, 502, 503, 529) or "429" in reason or "529" in reason
                if transient and _attempt + 1 < _RUNG_RETRIES:
                    time.sleep(_RUNG_BACKOFF_S)
                    continue
                break
        if reason:
            attempts.append({"provider": pid, "failed": reason})
            # model + reason so the cascade is legible in the cockpit (e.g. 429x2 → next).
            _trace("rung_fell", role=role, frm=pid, model=model, to="(next)",
                   reason=(f"429x{_RUNG_RETRIES} → next" if "429" in reason else reason))
            continue
        cost = 0.0 if free else _cost(prov, role, usage)
        if not free:
            _record_cost(pid, role, cost)
        # Surface the actual thinking tokens spent (role-aware budget, Fix 3) so the
        # planner's reasoning is visible in the cockpit / cost view.
        _details = usage.get("completion_tokens_details") or {}
        thinking_tok = int(_details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0)
        _trace("role_resolved", role=role, provider=pid, model=model, fell=len(attempts),
               thinking_budget=budget, thinking_tok=thinking_tok,
               out_tok=int(usage.get("completion_tokens", 0) or 0))
        return {"ok": True, "role": role, "role_active": True, "provider": pid, "model": model,
                "content": content, "usage": usage, "cost_usd": round(cost, 6),
                "free": free, "thinking_tok": thinking_tok, "fell": attempts}

    return {"ok": False, "proceed_local": True, "role": role, "role_active": True,
            "attempts": attempts,
            "reason": f"all {len(present)} present rung(s) for '{role}' failed or were "
                      "cap-blocked -> the driver proceeds local-only"}


# ── single-rung primitive (used by the frontier flow; caps enforced by caller) ─
def call_one(provider_id: str, role: str, messages: list[dict] | None = None, *,
             prompt: str | None = None, max_tokens: int | None = None,
             record: bool = True) -> dict[str, Any]:
    """Call ONE specific provider rung directly — no chain walk, no general USD cap
    (the caller, e.g. the three-gated frontier flow, enforces its OWN cap). Still
    presence-gated: returns {ok:False, proceed_local:True} if the provider is
    unknown, its key is absent, or it has no model for the role. Records the cost
    to the shared ledger when record=True so total spend stays visible to hm cost.
    Never raises."""
    if messages is None:
        messages = [{"role": "user", "content": prompt or ""}]
    cfg = reg.load_config()
    prov = cfg["providers"].get(provider_id)
    if not prov:
        return {"ok": False, "proceed_local": True, "reason": f"unknown provider '{provider_id}'"}
    key = os.environ.get(prov.get("env_key_name", ""), "").strip()
    if not key:
        return {"ok": False, "proceed_local": True,
                "reason": f"{provider_id} key ({prov.get('env_key_name')}) absent"}
    model = _model_for(prov, provider_id, role)
    if not model:
        return {"ok": False, "proceed_local": True,
                "reason": f"{provider_id} has no model for role '{role}'"}
    mt = max_tokens or MAX_TOKENS
    try:
        data, hdrs = _post_chat(prov["base_url"], key, model, messages, mt)
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        usage = data.get("usage", {}) or {}
        if content is None:
            raise ValueError("empty content")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "proceed_local": True, "provider": provider_id,
                "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    free = _is_free(prov, role)
    cost = 0.0 if free else _cost(prov, role, usage)
    if record and not free:
        _record_cost(provider_id, role, cost)
    _trace("call_one", provider=provider_id, role=role, model=model, cost_usd=round(cost, 6))
    return {"ok": True, "provider": provider_id, "model": model, "content": content,
            "usage": usage, "cost_usd": round(cost, 6), "free": free}


# ── UNORDERED parallel_draft FAN-OUT (RPM/RPD-budgeted, concurrent) ───────────
def _draft_one(entry: dict[str, str], prov: dict[str, Any], messages: list[dict],
               mt: int, env: dict[str, str]) -> dict[str, Any]:
    pid, model = entry["provider"], entry["model"]
    key = env.get(prov.get("env_key_name", ""), "")
    try:
        data, hdrs = _post_chat(prov["base_url"], key, model, messages, mt)
        _update_budget_from_headers(pid, model, hdrs)
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        usage = data.get("usage", {}) or {}
        if content is None:
            raise ValueError("empty content")
    except Exception as e:  # noqa: BLE001
        return {"provider": pid, "model": model, "ok": False,
                "error": f"{type(e).__name__}: {str(e)[:80]}"}
    free = _is_free(prov, "draft")
    cost = 0.0 if free else _cost(prov, "draft", usage)
    if not free:
        _record_cost(pid, "draft", cost)
    return {"provider": pid, "model": model, "ok": True, "content": content,
            "usage": usage, "cost_usd": round(cost, 6), "free": free}


def draft_fanout(messages: list[dict] | None = None, *, prompt: str | None = None,
                 n: int | None = None, max_tokens: int | None = None) -> dict[str, Any]:
    """Fan out a draft brief across the present parallel_draft pool, concurrently,
    respecting each provider's live RPM/RPD budget. Returns the raw candidates;
    selection is the verifier's job (mcp-search, Stage 4). Never raises."""
    if messages is None:
        messages = [{"role": "user", "content": prompt or ""}]
    cfg = reg.load_config()
    providers = cfg["providers"]
    caps = cfg["caps"]
    env = dict(os.environ)
    present = resolver.resolve_pool(cfg["draft_pool"], providers, env)
    cap_n = int(n or caps.get("draft_max_n", 5))

    # PRE-FLIGHT gate per entry (paid anchor obeys the USD cap; free members obey
    # per-MODEL TPM/RPM/RPD). Each entry's brief is fitted to the provider FIRST
    # (Groq input capped ~3.5K), then we skip — rather than 429 — if still over TPM.
    mt = max_tokens or MAX_TOKENS
    runnable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    ledger = _load_ledger()
    usd_blocked = _cap_blocked(caps, ledger)
    for entry in present:
        if len(runnable) >= cap_n:
            break
        prov = providers[entry["provider"]]
        model = entry["model"]
        if not _is_free(prov, "draft") and usd_blocked:
            skipped.append({**entry, "skipped": "usd_cap"})
            continue
        msgs, mt_eff, est = _prep_call(prov, model, messages, mt)
        ok_b, why = _budget_check(entry["provider"], model, prov, est, commit=True)
        if not ok_b:
            skipped.append({**entry, "skipped": f"{why}_exhausted"})
            continue
        runnable.append({"entry": entry, "prov": prov, "msgs": msgs, "mt": mt_eff})

    if not runnable:
        _trace("draft_fanout", n_present=len(present), n_runnable=0, degraded_local=True)
        return {"ok": False, "proceed_local": True, "candidates": [], "skipped": skipped,
                "n_present": len(present),
                "reason": "no pool member within budget -> degrade to N=1-local"}

    candidates: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(runnable)) as ex:
        futs = [ex.submit(_draft_one, r["entry"], r["prov"], r["msgs"], r["mt"], env)
                for r in runnable]
        for fut in concurrent.futures.as_completed(futs):
            candidates.append(fut.result())

    passed = [c for c in candidates if c.get("ok")]
    _trace("draft_fanout", n_present=len(present), n_runnable=len(runnable),
           n_passed=len(passed), n_skipped=len(skipped))
    return {"ok": bool(passed), "candidates": candidates, "skipped": skipped,
            "n_present": len(present), "n_runnable": len(runnable), "n_passed": len(passed),
            "reason": "fanned out across present free/cheap pool members for cross-family diversity"}


# ── status + cost report (Stage-5 surfaces) ──────────────────────────────────
def status() -> dict[str, Any]:
    cfg = reg.load_config()
    providers = cfg["providers"]
    env = dict(os.environ)
    roles = resolver.active_roles(cfg["role_chains"], providers, env)
    pool_present = resolver.resolve_pool(cfg["draft_pool"], providers, env)
    lg = _load_ledger()
    return {
        "mode": resolver.current_mode(env),
        "roles_active": roles,
        "resolved_chains": {r: resolver.resolve_chain(c, providers, env)
                            for r, c in cfg["role_chains"].items()},
        "draft_pool_present": [f"{e['provider']}:{e['model']}" for e in pool_present],
        "caps": cfg["caps"],
        "spend_today_usd": round(lg["spend_today"], 6),
        "spend_month_usd": round(lg["spend_month"], 6),
        "config_applied": cfg["config_applied"],
        "recent_falls": [t for t in _TRACE if t["event"] == "rung_fell"][-10:],
    }


def cost_report() -> dict[str, Any]:
    lg = _load_ledger()
    return {"date": lg["date"], "month": lg["month"],
            "spend_today_usd": round(lg["spend_today"], 6),
            "spend_month_usd": round(lg["spend_month"], 6),
            "calls": lg.get("calls", 0),
            "by_provider": lg.get("by_provider", {}),
            "by_role": lg.get("by_role", {})}
