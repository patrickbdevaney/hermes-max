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
    print(f"▸ active mode: {name}")
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
    print(f"{'MODE':<16}{'COST/MO':<10}{'GPU?':<6}POSTURE")
    cur = roles.active_mode_name()
    for name in roles.all_modes():
        m = roles.mode_meta(name)
        gpu = "yes" if m["requires_gpu"] else "no"
        posture = m["posture"].split(". ")[0]
        star = " *" if name == cur else ""
        print(f"{name + star:<16}{m['monthly_cost']:<10}{gpu:<6}{posture}")
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
    if cmd == "status-line":
        return cmd_status_line()
    print(f"unknown subcommand '{cmd}'")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
