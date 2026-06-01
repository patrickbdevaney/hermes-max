"""lib.inference — the SOLE seam between the harness and the model backends.

MCP servers request a ROLE; this fabric chooses a provider. Providers are config
(inference.yaml), not code. Missing keys silently drop rungs; zero keys = fully
local and free. The set of files:

    config.py    — inference.yaml loader (providers, models, costs, limits, tiers)
    roles.py     — roles.yaml + modes.yaml (role→chain, mode override, ceiling)
    buckets.py   — rate-bucket tracker (header parser + has_headroom; 429 avoidance)
    ledger.py    — central cost ledger ($0.000000, free-vs-paid split)
    adapters.py  — the only HTTP seam (openai_compatible + anthropic)
    router.py    — run_role(): walk the chain, first present+under-ceiling+has-headroom rung

Public API below. Nothing else in the repo should import a provider SDK or a
base URL directly.
"""
from __future__ import annotations

from . import config, ledger, roles
from .router import run_role, set_caller

__all__ = [
    "run_role", "set_caller",
    "present_providers", "active_mode_name", "set_mode", "all_modes",
    "mode_meta", "chain_for", "satisfiability", "cost_report",
]

present_providers = config.present_providers
active_mode_name = roles.active_mode_name
set_mode = roles.set_mode
all_modes = roles.all_modes
mode_meta = roles.mode_meta
chain_for = roles.chain_for
satisfiability = roles.satisfiability
cost_report = ledger.report
