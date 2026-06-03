#!/usr/bin/env python3
"""Regression test for the relevance-cascade COARSE rung (Phase 3.2).

Pins the contract of `research_core._relevance_prefilter` against a fixed 24-pair
fixture using a DETERMINISTIC toy embedder (bag-of-words cosine) monkeypatched in
for rank._embed — so it runs with no EMBED_BASE_URL / no network and is stable
across machines. Guards three properties that paid downstream rungs depend on:

  1. on-topic candidates outrank off-topic ones and survive the floor;
  2. the off-topic tail is dropped (count reported);
  3. a query is NEVER starved — at least keep_min survive even if all score low;
  4. degraded mode (embed backend unavailable) is an exact no-op.

No live services. Exit non-zero on first failure (mirrors smoke_test.py)."""
from __future__ import annotations

import re
import sys

import rank
import research_core as rc


def _ok(m): print(f"  ok: {m}")
def _fail(m): print(f"  FAIL: {m}"); sys.exit(1)


# ── deterministic toy embedder: bag-of-words over a fixed vocab ───────────────
_VOCAB = [
    "tcp", "congestion", "control", "window", "packet", "loss", "throughput",
    "network", "retransmit", "bandwidth", "latency", "protocol",            # on-topic
    "recipe", "garlic", "oven", "pasta", "basketball", "playoffs", "guitar", "chord",
]
_WORD = re.compile(r"[a-z]+")


def _toy_embed(texts):
    out = []
    for t in texts:
        toks = set(_WORD.findall(t.lower()))
        out.append([1.0 if v in toks else 0.0 for v in _VOCAB])
    return out


QUERY = "how does TCP congestion control adjust the window on packet loss"

# 24 candidates: 12 on-topic (varying overlap), 12 off-topic.
# Each on-topic line shares ≥1 query vocab term (tcp/congestion/control/window/
# packet/loss) so the toy cosine is well above the test floor; off-topic share none.
ON_TOPIC = [
    "TCP congestion control and the congestion window",
    "Packet loss triggers retransmit and window reduction",
    "Congestion control: detecting packet loss on the network",
    "Slow start and the congestion window protocol",
    "TCP window scaling and throughput",
    "Network congestion and packet loss",
    "Window scaling and TCP behavior",
    "Retransmit on packet loss in TCP",
    "Bandwidth and the congestion window",
    "Congestion control loop on loss events",
    "TCP control of the window",
    "Window, packet and loss in the protocol",
]
OFF_TOPIC = [
    "A garlic pasta recipe from the oven",
    "Basketball playoffs recap",
    "Learning your first guitar chord",
    "Best oven-roasted garlic recipe",
    "Pasta night: recipe ideas",
    "Playoffs schedule for basketball",
    "Guitar chord progressions for beginners",
    "Roasting garlic in the oven",
    "Recipe: pasta with basil",
    "Basketball training drills",
    "Acoustic guitar chord shapes",
    "Oven temperatures for pasta bakes",
]


def _candidates():
    return [{"url": f"https://ex/{i}", "title": t, "content": t}
            for i, t in enumerate(ON_TOPIC + OFF_TOPIC)]


def main() -> None:
    print("[R] relevance-cascade coarse rung regression")
    saved = rank._embed
    saved_floor = rc.RESEARCH_RELEVANCE_FLOOR
    try:
        rank._embed = _toy_embed  # type: ignore[assignment]
        # Pin a floor that cleanly separates "shares ≥1 query term" (>0) from "shares
        # none" (cosine 0) under the toy embedder, independent of the prod default.
        rc.RESEARCH_RELEVANCE_FLOOR = 0.10

        kept, dropped = rc._relevance_prefilter(QUERY, _candidates(), keep_min=3)
        kept_titles = {c["title"] for c in kept}

        # (1) on-topic survive, (2) off-topic dropped
        if any(t not in kept_titles for t in ON_TOPIC):
            _fail("an on-topic candidate was dropped by the floor")
        _ok(f"all {len(ON_TOPIC)} on-topic candidates survived")
        if any(t in kept_titles for t in OFF_TOPIC):
            _fail("an off-topic candidate survived the floor")
        _ok(f"all {len(OFF_TOPIC)} off-topic candidates filtered (dropped={dropped})")
        if dropped != len(OFF_TOPIC):
            _fail(f"dropped count {dropped} != {len(OFF_TOPIC)}")
        _ok("dropped count is exact and reported")

        # (3) never starve: all-irrelevant query still yields keep_min
        only_off = [{"url": f"https://o/{i}", "title": t, "content": t}
                    for i, t in enumerate(OFF_TOPIC)]
        kept2, _ = rc._relevance_prefilter(QUERY, only_off, keep_min=3)
        if len(kept2) != 3:
            _fail(f"starvation guard broken: kept {len(kept2)} (want 3)")
        _ok("never-starve guard holds (keep_min survivors even when all score 0)")

        # tiny input is a no-op (≤ keep_min)
        small = _candidates()[:3]
        kept3, dropped3 = rc._relevance_prefilter(QUERY, small, keep_min=3)
        if kept3 != small or dropped3 != 0:
            _fail("≤keep_min input was not a no-op")
        _ok("≤keep_min input is a no-op")
    finally:
        rank._embed = saved  # type: ignore[assignment]
        rc.RESEARCH_RELEVANCE_FLOOR = saved_floor

    # (4) degraded mode: embed backend unavailable → exact no-op
    saved2 = rank._embed
    try:
        rank._embed = lambda texts: None  # type: ignore[assignment]
        cands = _candidates()
        kept4, dropped4 = rc._relevance_prefilter(QUERY, cands, keep_min=3)
        if kept4 is not cands or dropped4 != 0:
            _fail("degraded (no-embed) mode is not an exact no-op")
        _ok("degraded mode (no EMBED_BASE_URL) is an exact no-op")
    finally:
        rank._embed = saved2  # type: ignore[assignment]

    print("[R] relevance regression: PASS")


if __name__ == "__main__":
    main()
