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
    if cmd == "status-line":
        return cmd_status_line()
    print(f"unknown subcommand '{cmd}'")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
