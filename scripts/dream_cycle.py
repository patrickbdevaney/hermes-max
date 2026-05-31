#!/usr/bin/env python3
"""dream_cycle.py — nightly corpus enrichment (Phase 5.2, the gbrain "dream cycle"),
a scheduled BACKGROUND job. Over the on-disk research corpus it:
  • dedups near-duplicate documents (quarantines the shorter — REVERSIBLE, never
    hard-deletes),
  • detects likely CONTRADICTIONS between topically-related docs (steer/local model;
    reports, never edits),
  • validates citations/provenance (flags docs missing source front-matter),
  • re-scores salience (length × recency).
Emits a dream_cycle_complete span {deduped, contradictions, citation_issues,
salience_scored}. Off the hot path; safe (reports/quarantines, doesn't destroy).

Run: python scripts/dream_cycle.py [--apply]   (default is dry-run for dedup moves)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

CORPUS = Path(os.path.expanduser(os.environ.get("RESEARCH_CORPUS_DIR", "~/.hermes-max/corpus")))
QUARANTINE = CORPUS / ".dream-quarantine"
LIVE = os.path.expanduser(os.path.join(os.environ.get("HERMES_MAX_LOG_DIR", "~/.hermes-max/logs"), "live.jsonl"))
ESCALATION_URL = os.environ.get("ESCALATION_MCP_URL",
                                f"http://127.0.0.1:{os.environ.get('MCP_ESCALATION_PORT','9105')}/mcp")
DEDUP_THRESHOLD = float(os.environ.get("DREAM_DEDUP_THRESHOLD", "0.85"))
MAX_CONTRADICTION_PAIRS = int(os.environ.get("DREAM_MAX_CONTRADICTION_PAIRS", "12"))
APPLY = "--apply" in sys.argv


def _shingles(text: str, n: int = 4) -> set:
    words = re.split(r"\s+", (text or "").lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))} if words else set()


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _docs() -> list[tuple[Path, str]]:
    out = []
    for p in CORPUS.rglob("*.md"):
        if QUARANTINE in p.parents:
            continue
        try:
            out.append((p, p.read_text(errors="replace")))
        except Exception:  # noqa: BLE001
            pass
    return out


def _steer_contradiction(a: str, b: str) -> bool | None:
    prompt = ("Do these two documents make DIRECTLY CONTRADICTORY factual claims (not "
              "merely different topics)? Answer strictly 'YES' or 'NO'.\n\n"
              f"DOC A:\n{a[:3000]}\n\nDOC B:\n{b[:3000]}")
    async def _go():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        box = {}
        try:
            async with streamablehttp_client(ESCALATION_URL) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.call_tool("conductor_steer", {"prompt": prompt, "max_tokens": 200})
                    txt = getattr(res.content[0], "text", "") if res.content else ""
                    d = res.structuredContent or (json.loads(txt) if txt else {})
                    box["v"] = d.get("result", d) if isinstance(d, dict) else {}
        except BaseException:  # noqa: BLE001
            if "v" in box:
                return box["v"]
            raise
        return box["v"]
    try:
        import asyncio
        d = asyncio.run(asyncio.wait_for(_go(), timeout=60))
        c = (d.get("content") or "") if isinstance(d, dict) and not d.get("proceed_local") else ""
        if c:
            return c.strip().upper().startswith("YES")
    except Exception:  # noqa: BLE001
        return None
    return None


def _emit(rec: dict) -> None:
    try:
        os.makedirs(os.path.dirname(LIVE), exist_ok=True)
        with open(LIVE, "a") as f:
            f.write(json.dumps({"ts": time.time(), "hms": time.strftime("%H:%M:%S"),
                                "kind": "span", "span": "dream_cycle_complete", **rec}) + "\n")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    docs = _docs()
    shings = {p: _shingles(t) for p, t in docs}
    texts = dict(docs)

    # 1) DEDUP — near-duplicate docs; keep the longer, quarantine the shorter (reversible)
    deduped, dup_pairs = 0, []
    paths = [p for p, _ in docs]
    quarantined: set = set()
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            pi, pj = paths[i], paths[j]
            if pi in quarantined or pj in quarantined:
                continue
            sim = _jaccard(shings[pi], shings[pj])
            if sim >= DEDUP_THRESHOLD:
                loser = pi if len(texts[pi]) <= len(texts[pj]) else pj
                dup_pairs.append((str(pi.name), str(pj.name), round(sim, 3), str(loser.name)))
                quarantined.add(loser)
                deduped += 1
                if APPLY:
                    QUARANTINE.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.move(str(loser), str(QUARANTINE / loser.name))
                    except Exception:  # noqa: BLE001
                        pass

    # 2) CITATION/provenance check — flag docs missing YAML front-matter source
    citation_issues = [p.name for p, t in docs if not (t.lstrip().startswith("---") and "source" in t[:600].lower())]

    # 3) CONTRADICTION detection — sample topically-related pairs (share a namespace)
    contradictions = []
    by_ns = {}
    for p, _ in docs:
        if p in quarantined:
            continue
        ns = p.parent.parent.name if p.parent.parent != CORPUS else p.parent.name
        by_ns.setdefault(ns, []).append(p)
    pairs, checked = [], 0
    for ns, ps in by_ns.items():
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                pairs.append((ps[i], ps[j]))
    for a, b in pairs[:MAX_CONTRADICTION_PAIRS]:
        v = _steer_contradiction(texts[a], texts[b])
        checked += 1
        if v:
            contradictions.append((a.name, b.name))

    # 4) SALIENCE re-score (length × recency)
    now = time.time()
    salience = {}
    for p, t in docs:
        if p in quarantined:
            continue
        age_days = max(0.001, (now - p.stat().st_mtime) / 86400)
        salience[str(p.relative_to(CORPUS))] = round(len(t) ** 0.5 / age_days, 3)
    try:
        (CORPUS / ".salience.json").write_text(json.dumps(salience, indent=2))
    except Exception:  # noqa: BLE001
        pass

    rec = {"deduped": deduped, "contradictions": len(contradictions),
           "citation_issues": len(citation_issues), "salience_scored": len(salience),
           "contradiction_pairs_checked": checked, "applied": APPLY, "docs": len(docs)}
    _emit(rec)
    print(json.dumps({**rec, "dup_pairs": dup_pairs, "contradiction_examples": contradictions[:5],
                      "citation_issue_files": citation_issues[:5]}, indent=2))
    if not APPLY and deduped:
        print(f"\n(dry-run: {deduped} duplicate(s) would be quarantined; re-run with --apply to move them)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
