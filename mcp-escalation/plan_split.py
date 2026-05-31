"""Plan/Execute split (CLAUDE_plan_execute.md) — the thin layer that routes a
substantive task through an up-front PLAN phase on the expensive planner before
the cheap local executor implements it.

Three concerns, one module (matches the repo's one-concern-per-file sizing):

  • plan_route          — given a task, advise the phase + tier + model_id and emit
                          a tier_routing span. PLAN phase -> the `synth` role
                          (DeepSeek V4-Pro); EXECUTE phase -> the local model. This
                          is ADVISORY (the conductor is tools + skills, not a
                          turn-level router); the local agent reads the advice and
                          calls brief_assemble(full) + conductor_synthesize itself.
  • plan_lint           — a DETERMINISTIC completeness gate over the PLAN.md
                          *document* (distinct from directive_verify, which gates a
                          JSON directive). Bounces a thin plan back to the planner.
  • request_plan_revision — when the executor hits a gap the plan did not answer,
                          route the specific question to the planner (synth/V4-Pro)
                          and append the answer to PLAN.md, rather than inventing.

Tier mapping (this codebase): the EXPENSIVE planner is the `synth` role
(deepseek-ai/DeepSeek-V4-Pro); `steer` is the CHEAP V4-Flash used for midway
execution grounding when the local executor is stuck — NOT the planner. So plan
generation and plan-gap revisions both route to synth.

GRACEFUL DEGRADATION is a hard requirement: every function returns a dict and
NEVER raises (mirrors conductor_core / directive_verify). Observability is
best-effort.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import conductor_core
import conductor_registry as reg
import escalation_core

# The synth model id is resolved LIVE from the registry (honors conductor.yaml),
# never hardcoded. This fallback is only used if the registry lookup fails.
_SYNTH_MODEL_FALLBACK = "deepseek-ai/DeepSeek-V4-Pro"

# Bounds (env-overridable). Counters are caller-passed PARAMETERS because MCP tools
# are stateless across calls — the SKILL increments and passes them (mirrors
# conductor_policy.subtask_budget_check).
PLAN_LINT_MAX_ROUNDS = int(os.environ.get("PLAN_LINT_MAX_ROUNDS", "2"))
PLAN_REVISION_MAX = int(os.environ.get("PLAN_REVISION_MAX", "3"))


def _otel(name: str, attrs: dict) -> None:
    """Best-effort OTel span. Never raises (observability is optional)."""
    try:
        import otel_emit

        otel_emit.record(name, attrs, status="ok")
    except Exception:  # noqa: BLE001 - observability is optional
        pass


def _synth_model_id() -> str:
    """The expensive planner's model id, live from the registry (conductor.yaml-aware)."""
    try:
        cfg = reg.load_config()
        m = cfg["providers"]["deepinfra"]["models"].get("synth")
        return m or _SYNTH_MODEL_FALLBACK
    except Exception:  # noqa: BLE001 - registry miss -> the known default
        return _SYNTH_MODEL_FALLBACK


# ── plan routing (Stage 1) ────────────────────────────────────────────────────
def plan_route(task: str = "", signals: dict | None = None,
               phase: str = "auto") -> dict[str, Any]:
    """Advise which model tier should handle the current phase, and emit a
    tier_routing span. Does NOT fire a model call.

    phase:
        'auto' (default) — classify the task; if it NEEDS_PLAN, advise the PLAN
            phase (synth/V4-Pro); otherwise advise EXECUTE (local).
        'plan'    — force PLAN-phase advice (synth/V4-Pro).
        'execute' — force EXECUTE-phase advice (local).

    Returns, for the PLAN phase:
        {ok, phase:"plan", tier:"synth", model_id:<registry synth>, plan_required,
         classification, how}
    For the EXECUTE phase:
        {ok, phase:"execute", tier:"local", plan_required, classification}
    Never raises.
    """
    phase = (phase or "auto").strip().lower()
    cls = escalation_core.classify_plan_need(task, signals)
    plan_required = bool(cls.get("plan_required"))

    want_plan = (phase == "plan") or (phase == "auto" and plan_required)

    if want_plan:
        model_id = _synth_model_id()
        _otel("tier_routing", {"phase": "plan", "tier": "synth", "model_id": model_id})
        return {"ok": True, "phase": "plan", "tier": "synth", "model_id": model_id,
                "plan_required": plan_required, "classification": cls,
                "how": ("generate PLAN.md on the expensive planner: "
                        "brief_assemble(profile='full') -> conductor_synthesize; "
                        "then plan_lint before the local executor begins")}

    _otel("tier_routing", {"phase": "execute", "tier": "local", "model_id": "local"})
    return {"ok": True, "phase": "execute", "tier": "local", "model_id": "local",
            "plan_required": plan_required, "classification": cls,
            "how": ("execute locally; on a design gap the plan did not answer, call "
                    "request_plan_revision (synth) rather than inventing")}


# ── PLAN.md document gate (Stage 2) ───────────────────────────────────────────
# Splits PLAN.md on markdown headers (the SAME idiom as brief_assemble._parse_plan)
# and validates the rich contract from workflow-plan-contract. This is DISTINCT from
# directive_verify, which gates a JSON directive against repo state — plan_lint
# checks the markdown DOCUMENT is complete enough that the executor never invents.
_HEADER_SPLIT = re.compile(r"(?m)^(#{1,6})\s+(.*)$")
# a file-ish token: has a slash or a known source/test extension
_FILE_TOKEN = re.compile(r"[\w./-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|rb|sh|ya?ml|toml|md|c|cpp|h)")
# a signature-shaped line in a FILE SPEC body
_SIG_LINE = re.compile(r"\bdef\s+\w+|\bclass\s+\w+|->|\bfn\s+\w+|\bfunction\s+\w+")
# concrete done-condition tokens (a number, or a verification verb)
_DONE_TOKEN = re.compile(r"\d|\b(test|tests|pass|passes|verify|green|coverage|assert)\b", re.I)
# strip signature scaffolding so what remains is the prose description, if any
_PARENS = re.compile(r"\([^()]*\)")
_RET_ANNO = re.compile(r"->\s*[\w\[\], .|]+")
_SIG_HEAD = re.compile(r"\b(?:def|class|fn|function)\s+\w+")


def _has_prose(body: str) -> bool:
    """A FILE SPEC has prose if any line, after its signature scaffolding (the
    def/class head, parenthesized params, and return annotation) is stripped, still
    carries >=4 descriptive words. This recognizes both a separate prose line AND a
    `def test_x — what it checks` line (signature + same-line description), while a
    bare typed signature alone (no description) does NOT count."""
    for ln in body.splitlines():
        s = ln.strip()
        if not s:
            continue
        s = _PARENS.sub(" ", s)        # drop (params)
        s = _RET_ANNO.sub(" ", s)      # drop -> ReturnType
        s = _SIG_HEAD.sub(" ", s)      # drop def/class NAME
        if len(re.findall(r"[A-Za-z]{2,}", s)) >= 4:
            return True
    return False


def _split_sections(text: str) -> list[tuple[int, str, str]]:
    """Return [(level, title, body), ...] for every markdown header in `text`,
    where level is the header depth (## -> 2). Mirrors brief_assemble._parse_plan's
    split (kept local — its return shape differs)."""
    blocks = _HEADER_SPLIT.split(text)
    out: list[tuple[int, str, str]] = []
    i = 1
    while i + 1 < len(blocks):
        level = len(blocks[i] or "")
        title = (blocks[i + 1] or "").strip()
        body = (blocks[i + 2] if i + 2 < len(blocks) else "").strip()
        out.append((level, title, body))
        i += 3
    return out


def _block_with_descendants(sections: list[tuple[int, str, str]], idx: int) -> str:
    """The full text owned by section `idx`: its own body PLUS every following
    DEEPER-level section's body, up to the next sibling-or-higher header. This makes
    a `## FILE SPEC:` block include its `### Class:` / `### Method` sub-sections — a
    strong planner naturally nests, and the signatures/prose live in those children."""
    level, _, body = sections[idx]
    parts = [body]
    for sub_level, _, sub_body in sections[idx + 1:]:
        if sub_level <= level:
            break
        parts.append(sub_body)
    return "\n".join(p for p in parts if p)


def plan_lint(plan_path: str = "", plan_text: str = "", repo: str | None = None,
              revision_round: int = 0) -> dict[str, Any]:
    """Deterministic completeness gate over a PLAN.md document (NO model call).

    Validates the workflow-plan-contract schema: an absolute WORKING_DIRECTORY; a
    FILES section; a FILE SPEC for every listed file; each FILE SPEC has both a
    signature-shaped line and prose; and a concrete DONE_CONDITION. An incomplete
    plan should be bounced BACK to the planner (synth) with `missing`, not handed to
    the executor.

    Args:
        plan_text: the PLAN.md content (takes precedence if given).
        plan_path / repo: else read from plan_path, else <repo|cwd>/PLAN.md.
        revision_round: the caller-tracked count of plan-revision rounds so far
            (MCP tools are stateless — the skill increments and passes it).

    Returns {ok, complete, missing:[...], revision_round, bounded, proceed_flagged,
    sections_found}. `bounded` is True once revision_round >= PLAN_LINT_MAX_ROUNDS,
    at which point the caller proceeds with a flagged-incomplete plan rather than
    looping forever. Never raises. Emits a plan_lint span.
    """
    text = plan_text
    if not text:
        path = plan_path or os.path.join(repo or os.getcwd(), "PLAN.md")
        try:
            text = Path(path).read_text()
        except Exception:  # noqa: BLE001 - missing/unreadable -> reported, never raised
            res = {"ok": True, "complete": False, "missing": ["PLAN.md not found"],
                   "revision_round": revision_round,
                   "bounded": revision_round >= PLAN_LINT_MAX_ROUNDS,
                   "proceed_flagged": revision_round >= PLAN_LINT_MAX_ROUNDS,
                   "sections_found": []}
            _otel("plan_lint", {"complete": False, "missing": "PLAN.md not found",
                                "revision_round": revision_round})
            return res

    sections = _split_sections(text)
    titles = [t for _, t, _ in sections]
    missing: list[str] = []

    # WORKING_DIRECTORY present AND absolute
    wd_body = next((b for _, t, b in sections if "working_directory" in t.lower()), None)
    if wd_body is None:
        missing.append("WORKING_DIRECTORY section missing")
    else:
        wd = wd_body.strip().splitlines()[0].strip() if wd_body.strip() else ""
        if not wd:
            missing.append("WORKING_DIRECTORY is empty")
        elif not os.path.isabs(wd):
            missing.append(f"WORKING_DIRECTORY is not an absolute path: '{wd}'")

    # FILES section + a FILE SPEC for every file it lists
    files_body = next((b for _, t, b in sections if t.strip().lower() == "files"
                       or t.strip().lower().startswith("files")), None)
    if files_body is None:
        missing.append("FILES section missing")
        listed_files: list[str] = []
    else:
        listed_files = []
        for line in files_body.splitlines():
            for m in _FILE_TOKEN.findall(line):
                if m not in listed_files:
                    listed_files.append(m)
        if not listed_files:
            missing.append("FILES section lists no files")

    spec_titles = [t for _, t, _ in sections if "file spec" in t.lower()]
    spec_blob = " ".join(spec_titles).lower()
    for f in listed_files:
        if f.lower() not in spec_blob:
            missing.append(f"no FILE SPEC for listed file '{f}'")

    # every FILE SPEC has a signature-shaped line AND prose — checked over the FILE
    # SPEC block INCLUDING its nested sub-sections (### Class:, ### Method, …), since
    # a strong planner naturally nests the signatures/prose under sub-headers.
    for idx, (_, t, _b) in enumerate(sections):
        if "file spec" not in t.lower():
            continue
        blob = _block_with_descendants(sections, idx)
        if not _SIG_LINE.search(blob):
            missing.append(f"FILE SPEC '{t}' has no function/class signature")
        if not _has_prose(blob):
            missing.append(f"FILE SPEC '{t}' has no prose describing the approach")

    # concrete DONE_CONDITION
    done_body = next((b for _, t, b in sections if "done_condition" in t.lower()
                      or "definition of done" in t.lower()), None)
    if done_body is None:
        missing.append("DONE_CONDITION section missing")
    elif not _DONE_TOKEN.search(done_body):
        missing.append("DONE_CONDITION is not concrete (no number or test/verify token)")

    complete = not missing
    bounded = revision_round >= PLAN_LINT_MAX_ROUNDS
    res = {"ok": True, "complete": complete, "missing": missing,
           "revision_round": revision_round, "bounded": bounded,
           "proceed_flagged": bounded and not complete,
           "sections_found": titles}
    _otel("plan_lint", {"complete": complete, "missing": "; ".join(missing)[:300],
                        "revision_round": revision_round})
    return res


# ── plan-gap revision (Stage 3) ───────────────────────────────────────────────
def request_plan_revision(question: str, repo: str | None = None, task_id: str = "",
                          request_index: int = 0,
                          max_tokens: int | None = None) -> dict[str, Any]:
    """Route a specific plan-gap question to the expensive planner (synth/V4-Pro)
    and append the answer to PLAN.md — the mechanism that lets the cheap executor
    ASK instead of INVENT when the plan is silent on a design decision.

    Args:
        question: the precise gap (a missing signature, an unspecified algorithm,
            an ambiguous edge case).
        repo: the project dir holding PLAN.md (default cwd).
        task_id: if given, enrich the synth prompt with brief_assemble(profile=full).
        request_index: caller-tracked count of revisions requested so far for THIS
            task (MCP tools are stateless — the skill increments and passes it).

    Returns {ok, resolved, answer, appended, request_index, bounded, proceed_flagged,
    model?, proceed_local?}. At request_index >= PLAN_REVISION_MAX no call fires
    (bounded). If synth is OFF/capped, returns proceed_local (the executor falls to
    workflow-stuck — it must NOT invent). Never raises. Emits a
    plan_revision_requested span.
    """
    repo = repo or os.getcwd()

    # bound first: at the cap, do NOT fire a call (avoid infinite planner/executor ping-pong)
    if request_index >= PLAN_REVISION_MAX:
        _otel("plan_revision_requested",
              {"question": question[:200], "resolved": False, "bounded": True})
        return {"ok": True, "resolved": False, "answer": "", "appended": False,
                "request_index": request_index, "bounded": True, "proceed_flagged": True,
                "reason": f"revision cap hit ({request_index} >= {PLAN_REVISION_MAX}) — "
                          "proceed best-effort with a flagged note, do NOT loop"}

    # build the planner prompt; optionally enrich with the deterministic full brief
    prompt = (
        "You are the PLANNER for a plan/execute split. The local executor hit a gap "
        "the PLAN.md did not answer and needs a precise, unambiguous answer so it can "
        "implement WITHOUT inventing. Answer ONLY this question, concretely (exact "
        "signature / formula / data structure / control flow / edge-case handling). "
        "Do NOT restate the whole plan.\n\n"
        f"QUESTION: {question}"
    )
    if task_id:
        try:
            import brief_assemble as brief

            b = brief.brief_assemble(task_id, current_blocker=question,
                                     decision_needed=question, profile="full", repo=repo)
            if b.get("ok"):
                prompt += "\n\n## CONTEXT (assembled brief)\n" + json.dumps(b["brief"])[:12000]
        except Exception:  # noqa: BLE001 - enrichment is best-effort
            pass

    r = conductor_core.run_role("synth", prompt=prompt, max_tokens=max_tokens)
    if not r.get("ok"):
        _otel("plan_revision_requested",
              {"question": question[:200], "resolved": False, "proceed_local": True})
        return {"ok": True, "resolved": False, "answer": "", "appended": False,
                "request_index": request_index, "bounded": False, "proceed_flagged": False,
                "proceed_local": True,
                "reason": r.get("reason", "synth role OFF/capped — proceed_local; the "
                          "executor must surface (workflow-stuck), not invent")}

    answer = r.get("content") or ""
    appended = False
    try:
        plan_path = Path(repo) / "PLAN.md"
        block = (f"\n\n## PLAN REVISION (answer to: {question.strip()})\n{answer}\n")
        with open(plan_path, "a") as f:
            f.write(block)
        appended = True
    except Exception:  # noqa: BLE001 - append best-effort; the skill can paste manually
        appended = False

    _otel("plan_revision_requested",
          {"question": question[:200], "resolved": True, "appended": appended})
    return {"ok": True, "resolved": True, "answer": answer, "appended": appended,
            "request_index": request_index, "bounded": False, "proceed_flagged": False,
            "model": r.get("model"), "provider": r.get("provider"),
            "cost_usd": r.get("cost_usd")}
