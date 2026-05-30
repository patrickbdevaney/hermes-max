#!/usr/bin/env python3
"""GEPA evolution of the difficulty-classifier prompt — the compounding loop.

GEPA (reflective prompt evolution) reads structured execution traces + textual
feedback, reflects with an LLM, and evolves the prompt along a Pareto frontier —
needs only ~10 examples / tens of evaluations (your inference host-viable), running ENTIRELY on
the local model as BOTH task_lm and reflection_lm.

Target: the difficulty classifier (mcp-escalation.classify_difficulty gates
search depth, verify depth, and escalation across the whole stack, so improving
it lifts everything). The deployed classifier is rule-based; here we evolve an
LLM classifier PROMPT to replicate/extend that policy from labelled traces, so
the local model can apply it where signals are fuzzy. Outputs a NEW versioned
prompt variant (never overwrites) and records before/after scores to the KG.

Run via run-evolution.sh (own venv). Bounded by MAX_METRIC_CALLS so a run is
minutes-to-hours, never days.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import dspy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import traces  # noqa: E402

try:
    import otel_emit  # best-effort spans
except Exception:  # noqa: BLE001
    class _N:
        @staticmethod
        def record(*a, **k):
            return {"ok": False}
    otel_emit = _N()  # type: ignore

VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "").rstrip("/")
MODEL = os.environ.get("DSPY_MODEL", "openai//model")
MAX_TOKENS = int(os.environ.get("DSPY_MAX_TOKENS", "6000"))
MAX_METRIC_CALLS = int(os.environ.get("MAX_METRIC_CALLS", "50"))
SKILLS_OUT = os.path.expanduser(
    os.environ.get("EVOLVE_SKILLS_DIR", "~/.hermes/skills/hermes-max"))
VARIANT_DIR = os.path.join(SKILLS_OUT, "classify-difficulty-prompt")
KG_MCP_URL = os.environ.get("KG_MCP_URL", "http://127.0.0.1:9103/mcp")
LABELS = ("easy", "medium", "hard")


class Difficulty(dspy.Signature):
    """Classify the difficulty of a coding task as easy, medium, or hard so the
    harness can gate search depth, verification, and escalation."""

    task: str = dspy.InputField(desc="task description, possibly with signals")
    difficulty: str = dspy.OutputField(desc="exactly one of: easy, medium, hard")


class Classifier(dspy.Module):
    def __init__(self):
        super().__init__()
        self.clf = dspy.Predict(Difficulty)

    def forward(self, task):
        return self.clf(task=task)


def _norm(x: str) -> str:
    x = (x or "").strip().lower()
    for c in LABELS:
        if c in x:
            return c
    return x


def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    want = _norm(gold.difficulty)
    got = _norm(getattr(pred, "difficulty", ""))
    score = 1.0 if got == want else 0.0
    if score == 1.0:
        fb = f"Correct ({want})."
    else:
        fb = (f"Wrong: predicted '{got or '?'}', correct '{want}'. Weigh PRIOR FAILURES "
              f"and NOVELTY as strongly as file count: a 1-file task with 2 prior failures "
              f"and high novelty is HARD, not easy; many files or cross-module spans raise "
              f"difficulty. Task: {gold.task}")
    return dspy.Prediction(score=score, feedback=fb)


def _to_examples(rows):
    return [dspy.Example(task=r["task"], difficulty=r["difficulty"]).with_inputs("task")
            for r in rows]


def _accuracy(program, devset) -> float:
    ok = 0
    for ex in devset:
        try:
            pred = program(task=ex.task)
            if _norm(pred.difficulty) == _norm(ex.difficulty):
                ok += 1
        except Exception:  # noqa: BLE001
            pass
    return ok / max(1, len(devset))


def _next_version(d: str) -> int:
    Path(d).mkdir(parents=True, exist_ok=True)
    vers = [int(p.stem.split("v")[-1]) for p in Path(d).glob("evolved.v*.md")
            if p.stem.split("v")[-1].isdigit()]
    return (max(vers) + 1) if vers else 1


def _record_kg(before: float, after: float, version: int, n_train: int) -> bool:
    try:
        import asyncio

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def go():
            async with streamablehttp_client(KG_MCP_URL) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    name = f"gepa-run-classifier-v{version}"
                    await s.call_tool("record_entity", {"type": "gepa_run", "name": name,
                        "props": {"target": "classify_difficulty", "before": before,
                                  "after": after, "lift": round(after - before, 3),
                                  "train_examples": n_train, "version": version}})
                    await s.call_tool("record_relation",
                        {"a": name, "rel": "evolved", "b": "classify-difficulty-prompt"})
        asyncio.run(go())
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    if not VLLM_BASE_URL:
        print("VLLM_BASE_URL unset — cannot run GEPA (needs the local model). Exit 0 (no-op).")
        return 0

    lm = dspy.LM(MODEL, api_base=VLLM_BASE_URL, api_key="x",
                 max_tokens=MAX_TOKENS, temperature=0.0)
    dspy.configure(lm=lm)

    rows = traces.all_examples(include_seed=True)
    real_n = traces.real_trace_count()
    print(f"examples: {len(rows)} total ({real_n} real, {len(rows) - real_n} seed); "
          f"MAX_METRIC_CALLS={MAX_METRIC_CALLS}")

    # split 70/30 (deterministic — no RNG; the harness blocks Math.random-style nondeterminism)
    cut = max(1, int(len(rows) * 0.7))
    trainset = _to_examples(rows[:cut])
    valset = _to_examples(rows[cut:]) or _to_examples(rows[:cut])

    base = Classifier()
    before = _accuracy(base, valset)
    print(f"baseline val accuracy: {before:.3f}")

    otel_emit.record("gepa_run_started",
                     {"target": "classify_difficulty", "examples": len(rows), "real": real_n})
    t0 = time.time()
    try:
        opt = dspy.GEPA(metric=metric, max_metric_calls=MAX_METRIC_CALLS,
                        reflection_lm=lm, track_stats=True, num_threads=1)
        evolved = opt.compile(base, trainset=trainset, valset=valset)
    except Exception as e:  # noqa: BLE001
        print(f"GEPA run failed: {type(e).__name__}: {e}")
        otel_emit.record("gepa_run_completed", {"ok": False, "error": str(e)[:200]}, status="error")
        return 1
    after = _accuracy(evolved, valset)
    dur = round(time.time() - t0, 1)
    print(f"evolved val accuracy: {after:.3f}  (lift {after - before:+.3f}, {dur}s)")

    # extract the evolved instruction (the prompt GEPA produced)
    try:
        instruction = evolved.clf.signature.instructions
    except Exception:  # noqa: BLE001
        instruction = Difficulty.__doc__ or ""

    version = _next_version(VARIANT_DIR)
    variant = Path(VARIANT_DIR) / f"evolved.v{version}.md"
    audit = Path(VARIANT_DIR) / f"evolved.v{version}.json"
    variant.write_text(
        f"# classify-difficulty prompt — GEPA-evolved variant v{version}\n\n"
        f"<!-- before={before:.3f} after={after:.3f} lift={after - before:+.3f} "
        f"real_traces={real_n} seed={len(rows) - real_n} max_metric_calls={MAX_METRIC_CALLS} -->\n\n"
        f"## Evolved instruction\n\n{instruction}\n")
    audit.write_text(json.dumps(
        {"version": version, "target": "classify_difficulty", "before": before,
         "after": after, "lift": after - before, "real_traces": real_n,
         "seed": len(rows) - real_n, "max_metric_calls": MAX_METRIC_CALLS,
         "duration_s": dur, "model": MODEL}, indent=2))
    print(f"wrote variant: {variant}")

    kg = _record_kg(before, after, version, len(trainset))
    otel_emit.record("gepa_run_completed",
                     {"ok": True, "before": before, "after": after,
                      "lift": after - before, "version": version, "kg_recorded": kg})
    otel_emit.record("skill_evolved", {"skill": "classify-difficulty-prompt", "version": version})

    if real_n == 0:
        print("NOTE: 0 real traces — this run used the SEED set to exercise the machinery. "
              "Real compounding needs accumulated session/escalation traces (the gate in "
              "run-evolution.sh enforces this for scheduled runs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
