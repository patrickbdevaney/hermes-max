"""Stage 6 — Banyan content-evolution (the long-horizon autonomy half).

THE HARD LINE, enforced in code: this loop may evolve CONTENT — which research
directions to explore, the RAG corpus, the KG, and the skill library — but NEVER
MACHINERY (no mcp-* server code, no Hermes core, no router, no tool .py/config). A
content write is allowed ONLY to a whitelisted root (corpus / skills / banyan state)
with a content extension (.md/.json/.jsonl/.txt); `_guard_content_write` refuses
anything else, and smoke_banyan.py asserts a full cycle writes no machinery file.

Pieces (all over the EXISTING namespaces/KG/RAG/skills — no new machinery):
  * banyan_select  — UCB1 explore-exploit over research namespaces:
       U_i = utility*priority + c*sqrt(ln(N)/n_i).  Unvisited namespaces get an
       infinite exploration bonus (visited despite lower utility). A pending human
       DIRECTIVE preempts selection (operator-in-the-loop seam).
  * banyan_update  — visit_count++, running utility (0.8 history / 0.2 new),
       marginal-gain history (last 20).
  * saturation     — two signals: (1) embedding-drift (new research too similar to
       the namespace corpus centroid => retreading) and (2) marginal-gain decline
       (last 10 trending down AND below threshold). On saturation: flag, STOP
       investing, and SURFACE TO THE OPERATOR — never silently churn.
  * standing tasks — when a namespace queue empties, generate research tasks (e.g.
       "what's new in {ns} since {last_ingest}") so unattended cycles never idle.
  * skill evolution — may write/refine markdown SKILLS (content), gated behind the
       maturity check (SELF_IMPROVEMENT_ENABLED + 200 tasks / 30 days / 50 skills).

Never raises; persistent state is JSON on disk under BANYAN_STATE_DIR.
"""
from __future__ import annotations

import datetime
import json
import math
import os
from typing import Any

try:
    import otel_emit
except Exception:  # noqa: BLE001
    class _NoOtel:
        @staticmethod
        def record(*_a, **_k):
            return {"ok": False}
    otel_emit = _NoOtel()  # type: ignore

import rank  # rank._embed / rank._cosine (shared embedding endpoint)

# ── config ────────────────────────────────────────────────────────────────────
BANYAN_STATE_DIR = os.path.expanduser(os.environ.get("BANYAN_STATE_DIR", "~/.hermes-max/banyan"))
SKILLS_DIR = os.path.expanduser(os.environ.get("BANYAN_SKILLS_DIR", "~/.hermes-max/skills"))
STATE_FILE = os.path.join(BANYAN_STATE_DIR, "state.json")
DIRECTIVE_FILE = os.path.join(BANYAN_STATE_DIR, "directive.json")
SURFACED_LOG = os.path.join(BANYAN_STATE_DIR, "surfaced.jsonl")

UCB_C = float(os.environ.get("BANYAN_UCB_C", "1.414"))
GAIN_HISTORY_MAX = 20
SATURATION_DRIFT_COSINE = float(os.environ.get("BANYAN_DRIFT_COSINE", "0.95"))  # >= => too similar
SATURATION_GAIN_FLOOR = float(os.environ.get("BANYAN_GAIN_FLOOR", "0.05"))
# Empty-base correctness: never flag a namespace SATURATED on thin data — saturation
# detection is DISABLED below this many recorded tasks/namespace (Stage-6 gate).
SATURATION_MIN_HISTORY = int(os.environ.get("BANYAN_SATURATION_MIN_HISTORY", "10"))
# RISK-A remedy (Stage-6): UCB1 is a stationary-bandit explorer — good for RESEARCH
# breadth, bad for BUILD-loop focus (it abandons half-finished hard subtasks for
# shinier easy ones). BANYAN_SCOPE scopes UCB1:
#   research_only (DEFAULT) — UCB1 governs research-namespace selection ONLY; the
#       build loop uses finish-what-you-started / dependency-order (select_build_subtask).
#   all — UCB1 governs both loops (the thrash-prone behaviour; kept for A/B eval).
BANYAN_SCOPE = os.environ.get("BANYAN_SCOPE", "research_only").strip().lower()
SELF_IMPROVEMENT_ENABLED = os.environ.get("SELF_IMPROVEMENT_ENABLED", "false").strip().lower() in ("1", "true", "yes")
MATURITY_MIN_TASKS = int(os.environ.get("BANYAN_MIN_TASKS", "200"))
MATURITY_MIN_DAYS = int(os.environ.get("BANYAN_MIN_DAYS", "30"))
MATURITY_MIN_SKILLS = int(os.environ.get("BANYAN_MIN_SKILLS", "50"))

# Content-write whitelist (the machinery guard).
_CONTENT_EXT = (".md", ".json", ".jsonl", ".txt")
_CONTENT_ROOTS = (BANYAN_STATE_DIR, SKILLS_DIR,
                  os.path.expanduser(os.environ.get("RESEARCH_CORPUS_DIR", "~/.hermes-max/corpus")))


def _now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ══ THE MACHINERY GUARD ═══════════════════════════════════════════════════════
def is_machinery_path(path: str) -> bool:
    """True if `path` is MACHINERY (must never be written by the loop): any code/
    config (.py/.yaml/.toml/.cfg/.ini/.sh/.txt-outside-content), or anything under
    an mcp-* server dir / lib / serving / scripts. Used by both the guard and the
    Stage-6 no-machinery-write assertion."""
    ap = os.path.abspath(path)
    if ap.endswith((".py", ".pyc", ".pyi", ".yaml", ".yml", ".toml", ".cfg", ".ini",
                    ".sh", ".lock", ".so")):
        return True
    parts = ap.split(os.sep)
    if any(p.startswith("mcp-") for p in parts) or any(
            p in ("lib", "serving", "scripts", "migration", "hermes-config", "kg") for p in parts):
        return True
    return False


def _guard_content_write(path: str) -> dict[str, Any]:
    """Allow a write ONLY to a whitelisted content root with a content extension and
    NOT a machinery path. Returns {ok} or {ok:False, error} — callers refuse on False."""
    ap = os.path.abspath(path)
    if is_machinery_path(ap):
        return {"ok": False, "error": f"refused: '{path}' is MACHINERY (loop evolves content only)"}
    if not ap.endswith(_CONTENT_EXT):
        return {"ok": False, "error": f"refused: '{path}' is not a content file {_CONTENT_EXT}"}
    if not any(ap.startswith(os.path.abspath(r)) for r in _CONTENT_ROOTS):
        return {"ok": False, "error": f"refused: '{path}' outside content roots"}
    return {"ok": True}


def _write_content(path: str, text: str) -> dict[str, Any]:
    g = _guard_content_write(path)
    if not g["ok"]:
        otel_emit.record("machinery_write_refused", {"path": path})
        return g
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "path": path}


# ── persistent state ──────────────────────────────────────────────────────────
def _load_state() -> dict[str, Any]:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"namespaces": {}}


def _save_state(state: dict[str, Any]) -> dict[str, Any]:
    return _write_content(STATE_FILE, json.dumps(state, indent=2))


def _ns(state: dict, name: str) -> dict[str, Any]:
    return state["namespaces"].setdefault(name, {
        "visit_count": 0, "utility": 0.0, "priority": 1.0, "gain_history": [],
        "saturated": False, "last_ingest": None, "queue": [], "centroid": None})


def register_namespace(name: str, priority: float = 1.0) -> dict[str, Any]:
    state = _load_state()
    ns = _ns(state, name)
    ns["priority"] = float(priority)
    _save_state(state)
    return {"ok": True, "namespace": name, "priority": ns["priority"]}


# ── directive interrupt (operator-in-the-loop seam) ───────────────────────────
def set_directive(text: str, namespace: str | None = None) -> dict[str, Any]:
    """Operator drops a directive; it preempts UCB1 on the next cycle."""
    return _write_content(DIRECTIVE_FILE, json.dumps(
        {"directive": text, "namespace": namespace, "set_at": _now_iso()}))


def pending_directive() -> dict[str, Any] | None:
    try:
        with open(DIRECTIVE_FILE) as f:
            d = json.load(f)
        return d if d.get("directive") else None
    except Exception:  # noqa: BLE001
        return None


def clear_directive() -> dict[str, Any]:
    try:
        if os.path.exists(DIRECTIVE_FILE):
            os.remove(DIRECTIVE_FILE)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


# ── UCB1 selection ─────────────────────────────────────────────────────────────
def banyan_select(c: float = UCB_C) -> dict[str, Any]:
    """Pick the next research direction. A pending DIRECTIVE preempts (human steer).
    Otherwise UCB1 over NON-saturated namespaces: unvisited => infinite exploration
    bonus (picked first); else utility*priority + c*sqrt(ln(N)/n_i)."""
    directive = pending_directive()
    if directive:
        otel_emit.record("directive_interrupt", {"namespace": directive.get("namespace")})
        return {"ok": True, "mode": "directive", "directive": directive["directive"],
                "namespace": directive.get("namespace"), "preempted_ucb1": True}

    state = _load_state()
    candidates = {n: v for n, v in state["namespaces"].items() if not v.get("saturated")}
    if not candidates:
        return {"ok": True, "mode": "idle", "namespace": None,
                "reason": "no non-saturated namespaces"}
    total_visits = sum(v["visit_count"] for v in candidates.values())
    N = max(1, total_visits)
    scores: dict[str, float] = {}
    for name, v in candidates.items():
        n_i = v["visit_count"]
        if n_i == 0:
            scores[name] = float("inf")  # explore the unvisited first
            continue
        exploit = v["utility"] * v.get("priority", 1.0)
        explore = c * math.sqrt(math.log(N) / n_i)
        scores[name] = exploit + explore
    chosen = max(scores, key=lambda k: scores[k])
    otel_emit.record("banyan_selected", {"namespace": chosen,
                                         "ucb_score": None if scores[chosen] == float("inf") else round(scores[chosen], 4),
                                         "visits": candidates[chosen]["visit_count"]})
    return {"ok": True, "mode": "explore", "namespace": chosen,
            "ucb_scores": {k: ("inf" if s == float("inf") else round(s, 4)) for k, s in scores.items()},
            "visit_count": candidates[chosen]["visit_count"]}


# ── RISK-A remedy: BUILD-loop selection (finish-what-you-started, NOT UCB1) ────
def select_build_subtask(subtasks: list[dict], in_progress: str | None = None) -> dict[str, Any]:
    """Pick the next BUILD subtask WITHOUT UCB1 — the build loop needs sustained
    focus, so coherent building is finish-what-you-started then dependency-order:
      1. if a subtask is already in progress and incomplete, KEEP it (never switch
         away from half-finished work for a shinier one — the anti-thrash rule);
      2. else the first INCOMPLETE subtask whose deps are all complete (dep order);
      3. else None (all complete / blocked).
    Each subtask: {id, status:'complete'|'incomplete', deps:[ids]}. This is what
    BANYAN_SCOPE=research_only routes the build loop to, instead of banyan_select()."""
    by_id = {t["id"]: t for t in subtasks}

    def done(tid: str) -> bool:
        return by_id.get(tid, {}).get("status") == "complete"

    incomplete = [t for t in subtasks if t.get("status") != "complete"]
    if not incomplete:
        return {"ok": True, "subtask": None, "reason": "all subtasks complete", "switched": False}
    if in_progress and in_progress in by_id and not done(in_progress):
        return {"ok": True, "subtask": in_progress, "switched": False,
                "strategy": "finish_in_progress", "reason": "finish-what-you-started (no switch)"}
    ready = [t for t in incomplete if all(done(d) for d in t.get("deps", []))]
    pick = (ready or incomplete)[0]
    return {"ok": True, "subtask": pick["id"], "strategy": "dependency_order",
            "switched": bool(in_progress and in_progress != pick["id"]),
            "reason": "dependency-order (deps satisfied)" if ready else "oldest incomplete (deps unmet)"}


def select_next(loop: str, *, subtasks: list[dict] | None = None,
                in_progress: str | None = None, c: float = UCB_C) -> dict[str, Any]:
    """Route selection by loop + BANYAN_SCOPE (the RISK-A config split):
      • BANYAN_SCOPE=research_only (DEFAULT): the BUILD loop uses finish-what-you-
        started (select_build_subtask, no UCB1 thrash); RESEARCH uses UCB1.
      • BANYAN_SCOPE=all: UCB1 (banyan_select) governs BOTH loops (thrash-prone)."""
    if loop == "build" and BANYAN_SCOPE == "research_only":
        out = select_build_subtask(subtasks or [], in_progress)
        out["selector"] = "build:finish-what-you-started"
        return out
    sel = banyan_select(c)
    sel["selector"] = f"{loop}:ucb1"
    return sel


# ── update after a task completes ──────────────────────────────────────────────
def banyan_update(namespace: str, utility_sample: float, gain: float) -> dict[str, Any]:
    """After a research/skill task: visit_count++, running utility (0.8 hist / 0.2
    new), append marginal gain (keep last 20)."""
    state = _load_state()
    ns = _ns(state, namespace)
    ns["visit_count"] += 1
    ns["utility"] = round(0.8 * ns["utility"] + 0.2 * float(utility_sample), 6)
    ns["gain_history"] = (ns["gain_history"] + [round(float(gain), 6)])[-GAIN_HISTORY_MAX:]
    ns["last_ingest"] = _now_iso()
    _save_state(state)
    otel_emit.record("banyan_updated", {"namespace": namespace, "visits": ns["visit_count"],
                                        "utility": ns["utility"]})
    return {"ok": True, "namespace": namespace, "visit_count": ns["visit_count"],
            "utility": ns["utility"], "gain_history_len": len(ns["gain_history"])}


# ── saturation detection (two signals) + surface to operator ──────────────────
def surface_to_operator(message: str, detail: dict | None = None) -> dict[str, Any]:
    """Append to the sovereign operator-surface log (Telegram optional on top). This
    is how saturation/decisions reach a human — never silently churned."""
    line = json.dumps({"at": _now_iso(), "message": message, "detail": detail or {}})
    try:
        os.makedirs(os.path.dirname(SURFACED_LOG), exist_ok=True)
        with open(SURFACED_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass
    otel_emit.record("operator_surfaced", {"message": message})
    return {"ok": True, "surfaced": True, "message": message}


def detect_saturation(namespace: str, new_texts: list[str] | None = None) -> dict[str, Any]:
    """Two signals. (1) embedding-drift: new research too SIMILAR to the namespace
    corpus centroid (mean cosine >= drift threshold => retreading). (2) marginal-gain
    decline: last 10 gains trending down AND mean below the floor. On saturation:
    flag, STOP investing, SURFACE TO OPERATOR. (Spec's '< threshold' wording is the
    inverse of 'too similar'; implemented as high-similarity = retreading.)"""
    state = _load_state()
    ns = _ns(state, namespace)
    reasons: list[str] = []
    # Empty-base gate: below the minimum history we still SEED the centroid (so drift
    # works once mature) but NEVER flag saturated — thin data must not stop investment.
    enough_history = ns.get("visit_count", 0) >= SATURATION_MIN_HISTORY

    # (1) embedding drift vs stored centroid
    drift_sim = None
    if new_texts:
        vecs = rank._embed([t for t in new_texts if t and t.strip()])
        if vecs:
            new_centroid = [sum(col) / len(vecs) for col in zip(*vecs)]
            if ns.get("centroid"):
                drift_sim = rank._cosine(new_centroid, ns["centroid"])
                if drift_sim >= SATURATION_DRIFT_COSINE:
                    reasons.append(f"embedding-drift: mean cosine {drift_sim:.3f} >= {SATURATION_DRIFT_COSINE} (retreading)")
            ns["centroid"] = new_centroid  # update running centroid

    # (2) marginal-gain decline
    gains = ns["gain_history"][-10:]
    if len(gains) >= 4:
        first_half = sum(gains[:len(gains) // 2]) / (len(gains) // 2)
        second_half = sum(gains[len(gains) // 2:]) / (len(gains) - len(gains) // 2)
        # diminishing returns NOW = recent gains both trending down AND themselves
        # below the floor (so a topic that was hot but has gone quiet is caught).
        if second_half < first_half and second_half < SATURATION_GAIN_FLOOR:
            reasons.append(f"marginal-gain decline: recent {second_half:.3f} < earlier {first_half:.3f} and below floor {SATURATION_GAIN_FLOOR}")

    note = None
    if not enough_history:
        # thin data — suppress any signal; keep investing until history is sufficient
        note = (f"saturation disabled below {SATURATION_MIN_HISTORY} tasks "
                f"(have {ns.get('visit_count', 0)}) — never flag on thin data")
        reasons = []
    saturated = bool(reasons) and enough_history
    if saturated:
        ns["saturated"] = True
        surface_to_operator(f"namespace '{namespace}' SATURATED — stopping investment, awaiting direction",
                            {"namespace": namespace, "reasons": reasons})
        otel_emit.record("saturation_flagged", {"namespace": namespace, "reasons": len(reasons)})
    _save_state(state)
    return {"ok": True, "namespace": namespace, "saturated": saturated, "reasons": reasons,
            "drift_similarity": drift_sim, "note": note,
            "min_history": SATURATION_MIN_HISTORY, "visit_count": ns.get("visit_count", 0)}


# ── standing-task generation (never idle) ─────────────────────────────────────
def generate_standing_tasks(namespace: str) -> dict[str, Any]:
    """When a namespace's queue empties, generate standing RESEARCH tasks (content,
    never machinery) so unattended cycles never idle."""
    state = _load_state()
    ns = _ns(state, namespace)
    if ns["queue"]:
        return {"ok": True, "namespace": namespace, "queue": ns["queue"], "generated": 0}
    since = ns.get("last_ingest") or "the beginning"
    tasks = [f"what's new in {namespace} since {since}",
             f"open problems and contradictions in {namespace}",
             f"most-cited recent work in {namespace}"]
    ns["queue"] = tasks
    _save_state(state)
    otel_emit.record("standing_tasks_generated", {"namespace": namespace, "n": len(tasks)})
    return {"ok": True, "namespace": namespace, "queue": tasks, "generated": len(tasks)}


def pop_task(namespace: str) -> dict[str, Any]:
    state = _load_state()
    ns = _ns(state, namespace)
    task = ns["queue"].pop(0) if ns["queue"] else None
    _save_state(state)
    return {"ok": True, "namespace": namespace, "task": task, "remaining": len(ns["queue"])}


# ── runtime skill evolution (gated, CONTENT only) ─────────────────────────────
def can_evolve_skills(tasks_done: int = 0, days_active: int = 0,
                      skills_count: int = 0) -> dict[str, Any]:
    """Maturity gate — runtime skill evolution stays OFF until the system has earned
    it (SELF_IMPROVEMENT_ENABLED + 200 tasks / 30 days / 50 skills)."""
    reasons = []
    if not SELF_IMPROVEMENT_ENABLED:
        reasons.append("SELF_IMPROVEMENT_ENABLED=false")
    if tasks_done < MATURITY_MIN_TASKS:
        reasons.append(f"tasks {tasks_done}<{MATURITY_MIN_TASKS}")
    if days_active < MATURITY_MIN_DAYS:
        reasons.append(f"days {days_active}<{MATURITY_MIN_DAYS}")
    if skills_count < MATURITY_MIN_SKILLS:
        reasons.append(f"skills {skills_count}<{MATURITY_MIN_SKILLS}")
    return {"ok": True, "allowed": not reasons, "blocking": reasons}


def write_skill(name: str, content: str, tasks_done: int = 0, days_active: int = 0,
                skills_count: int = 0) -> dict[str, Any]:
    """Write/refine a markdown SKILL (content) into the skill library — gated by the
    maturity check AND the machinery guard (a non-.md / machinery path is refused)."""
    gate = can_evolve_skills(tasks_done, days_active, skills_count)
    if not gate["allowed"]:
        return {"ok": False, "error": "skill evolution gated", "blocking": gate["blocking"]}
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name.lower()).strip("-") or "skill"
    path = os.path.join(SKILLS_DIR, f"{slug}.md")
    w = _write_content(path, content)  # guard ensures .md under skills root only
    if w["ok"]:
        otel_emit.record("skill_evolved", {"name": slug})
    return {"ok": w["ok"], "path": w.get("path"), "error": w.get("error")}


# ── one unattended cycle (selection only — the agent runs the research) ───────
def next_action() -> dict[str, Any]:
    """Top of an unattended cycle: directive interrupt OR Banyan self-direction.
    Returns the action for the agent to execute; this module never runs research or
    touches machinery itself."""
    sel = banyan_select()
    if sel.get("mode") == "explore" and sel.get("namespace"):
        st = generate_standing_tasks(sel["namespace"])  # ensure the queue isn't empty
        sel["next_task"] = st["queue"][0] if st["queue"] else None
    return sel


def banyan_stats() -> dict[str, Any]:
    state = _load_state()
    return {"state_dir": BANYAN_STATE_DIR, "skills_dir": SKILLS_DIR,
            "namespaces": {n: {"visits": v["visit_count"], "utility": v["utility"],
                               "saturated": v["saturated"]}
                           for n, v in state.get("namespaces", {}).items()},
            "self_improvement_enabled": SELF_IMPROVEMENT_ENABLED,
            "ucb_c": UCB_C, "content_roots": list(_CONTENT_ROOTS)}
