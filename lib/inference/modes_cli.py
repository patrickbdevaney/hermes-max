"""`hm mode` backend — print / switch / list modes. Human-readable output.

Usage (driven by the `hm mode` bash verb):
    python3 -m lib.inference.modes_cli show          # current mode + posture + coverage
    python3 -m lib.inference.modes_cli set <name>    # switch live, persist, validate
    python3 -m lib.inference.modes_cli list          # the appeal-ordered table
    python3 -m lib.inference.modes_cli status-line    # compact 'mode · $today' for the cockpit
"""
from __future__ import annotations

import sys

from . import ledger, roles


def _coverage_line(name: str) -> list[str]:
    sat = roles.satisfiability(name)
    out = []
    for role, rung in sat["roles"].items():
        mark = rung if rung else "— (degrades to local)"
        out.append(f"    {role:<16} {mark}")
    return out


def cmd_show() -> int:
    name = roles.active_mode_name()
    m = roles.mode_meta(name)
    sat = roles.satisfiability(name)
    print(f"▸ active mode: {name}   [free-uplift: {_uplift_status()}]")
    print(f"  cost/mo: {m['monthly_cost']}   needs GPU: {'yes' if m['requires_gpu'] else 'no'}"
          f"   ceiling: {m['inference_mode']}")
    print(f"  {m['posture']}")
    print("  role coverage (first present rung under ceiling):")
    for line in _coverage_line(name):
        print(line)
    for w in sat["warnings"]:
        print(f"  ⚠ {w}")
    return 0


def cmd_set(name: str) -> int:
    res = roles.set_mode(name)
    if not res.get("ok"):
        print(f"✗ {res.get('error')}; available: {', '.join(res.get('available', []))}")
        return 2
    m = roles.mode_meta(name)
    print(f"✓ mode → {name}  ({m['monthly_cost']}/mo, "
          f"GPU {'required' if m['requires_gpu'] else 'not needed'}, "
          f"ceiling {m['inference_mode']})")
    for w in res.get("warnings", []):
        print(f"  ⚠ {w}")
    print("  (applies to the next task — no restart needed)")
    return 0


def cmd_list() -> int:
    print(f"{'MODE':<16}{'COST/MO':<10}{'GPU?':<6}WHAT IT DOES")
    cur = roles.active_mode_name()
    for name in roles.all_modes():
        m = roles.mode_meta(name)
        gpu = "yes" if m["requires_gpu"] else "no"
        # the taxonomy tagline (one crisp line); fall back to the posture's first sentence.
        desc = m.get("tagline") or m["posture"].split(". ")[0]
        star = " *" if name == cur else ""
        print(f"{name + star:<16}{m['monthly_cost']:<10}{gpu:<6}{desc}")
    print("\n  * = active. Switch with: hm mode <name>")
    return 0


def cmd_ceiling(name: str) -> int:
    """Print just the spend ceiling (local|free|full|frontier) for `hm` to sync
    CONDUCTOR_MODE."""
    print(roles.mode_meta(name or roles.active_mode_name())["inference_mode"])
    return 0


def cmd_meta(name: str) -> int:
    """Print `ceiling requires_gpu` (e.g. 'full 1') for shell parsing."""
    m = roles.mode_meta(name or roles.active_mode_name())
    print(f"{m['inference_mode']} {1 if m['requires_gpu'] else 0}")
    return 0


def cmd_providers() -> int:
    """Present/absent table for every provider in inference.yaml — what each enables
    and how to turn it on. Used by setup.sh and `hm preflight`."""
    from . import config
    present = config.present_providers()
    print(f"{'PROVIDER':<18}{'STATUS':<10}{'TIER':<10}ENABLE / COST")
    for name, block in config.providers().items():
        ok = name in present
        status = "● present" if ok else "○ absent"
        keyenv = block.get("api_key_env") or "(keyless)"
        cost = block.get("cost") or {}
        if config.tier(name) == "local":
            note = "local vLLM — free, private"
        elif config.tier(name) == "free":
            note = f"set {keyenv} — free tier"
        elif config.tier(name) == "frontier":
            note = f"set {keyenv} — ${cost.get('in_per_mtok','?')}/${cost.get('out_per_mtok','?')} per M (spare frontier)"
        else:
            note = f"set {keyenv} — ${cost.get('in_per_mtok','?')}/${cost.get('out_per_mtok','?')} per M"
        print(f"{name:<18}{status:<10}{config.tier(name):<10}{note}")
    gw = config.get_default_gateway()
    if gw:
        ok = config.gateway_present()
        status = "● present" if ok else "○ absent"
        print(f"{'default_gateway':<18}{status:<10}{config.gateway_tier():<10}"
              f"set {gw.get('api_key_env')} — catch-all when all named rungs are gone "
              f"({gw.get('default_model')})")
    return 0


def cmd_executor(name: str) -> int:
    """Print the agent-loop backend for a mode as shell-eval-able vars (consumed by
    scripts/set_mode.sh to write ~/.hermes/config.yaml). Never prints the secret —
    only the api_key_env NAME; the shell resolves the value itself."""
    b = roles.executor_backend(name or roles.active_mode_name())
    print(f"HERMES_EXEC_PROVIDER={b['provider']}")
    print(f"HERMES_EXEC_MODEL_ID={b['model_id']}")
    print(f"HERMES_EXEC_BASE_URL={b['base_url']}")
    print(f"HERMES_EXEC_API_KEY_ENV={b['api_key_env']}")
    print(f"HERMES_EXEC_LOCAL={1 if b['local'] else 0}")
    print(f"HERMES_EXEC_PRESENT={1 if b['present'] else 0}")
    return 0


def _uplift_status() -> str:
    from . import config
    v = (config._effective_env(None).get("INFERENCE_MODE_FREE_UPLIFT", "") or "").lower()
    return "ON" if v in ("1", "true", "yes", "on") else "OFF"


def cmd_cost(window: str = "today") -> int:
    """Render the fabric ledger ($0.000000) — totals, by provider/model/role, the
    free-vs-paid split, remaining free RPD, active mode + free_uplift status."""
    import os

    from . import buckets, config
    rep = ledger.report(window)
    mode = roles.active_mode_name()
    print(f"═══ inference cost — {window} ═══")
    print(f"  mode: {mode}   free-uplift: {_uplift_status()}   "
          f"calls: {rep['calls']}   TOTAL: {ledger.fmt_usd(rep['total_usd'])}")
    print(f"  free vs paid: {rep['free_tok']:,} tok @ $0  |  "
          f"{rep['paid_tok']:,} tok paid")

    def table(title: str, d: dict, key_w: int = 26) -> None:
        if not d:
            return
        print(f"  ── by {title} ──")
        for k, e in sorted(d.items(), key=lambda kv: -kv[1]["usd"]):
            print(f"    {k:<{key_w}} {ledger.fmt_usd(e['usd'])}  "
                  f"{e['tok']:>10,} tok  {e['calls']:>4} calls")

    table("provider", rep["by_provider"], 16)
    table("model", rep["by_model"], 30)
    table("role", rep["by_role"], 20)

    up = rep["by_role"].get("free_uplift")
    if up:
        print(f"  ── free_uplift ──  {up['calls']} calls  {up['tok']:,} tok  "
              f"{ledger.fmt_usd(up['usd'])}")

    if rep["free_budget_remaining"]:
        print("  ── remaining FREE budget today (RPD) ──")
        for slot, rem in sorted(rep["free_budget_remaining"].items()):
            print(f"    {slot:<30} {rem if rem is not None else '∞':>8} req left")
    return 0


def cmd_status_line() -> int:
    name = roles.active_mode_name()
    rep = ledger.report("today")
    print(f"mode {name} · {ledger.fmt_usd(rep['total_usd'])} today "
          f"({rep['calls']} calls)")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "show"
    if cmd == "show":
        return cmd_show()
    if cmd == "set":
        if len(argv) < 2:
            print("usage: modes_cli set <name>")
            return 2
        return cmd_set(argv[1])
    if cmd == "list":
        return cmd_list()
    if cmd == "name":
        print(roles.active_mode_name())
        return 0
    if cmd == "ceiling":
        return cmd_ceiling(argv[1] if len(argv) > 1 else "")
    if cmd == "meta":
        return cmd_meta(argv[1] if len(argv) > 1 else "")
    if cmd == "providers":
        return cmd_providers()
    if cmd == "cost":
        return cmd_cost(argv[1] if len(argv) > 1 else "today")
    if cmd == "executor":
        return cmd_executor(argv[1] if len(argv) > 1 else "")
    if cmd == "status-line":
        return cmd_status_line()
    print(f"unknown subcommand '{cmd}'")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
