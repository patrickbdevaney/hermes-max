"""load_plugins — register optional plugins at `hm up`, if their conditions hold.

Run standalone (`python3 -m plugins.load_plugins`) it prints each plugin's
registration decision (used by `hm up` for logging). Passed a `conductor` object
that exposes `register_post_verify_hook`, it wires the hooks in-process. The core
never imports a plugin; plugins register against the conductor's generic hook.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def load_plugins(conductor: Optional[Any] = None,
                 env: Optional[dict[str, str]] = None) -> list[tuple[str, str]]:
    """Return [(plugin_name, status)]. Registers hooks if `conductor` is given."""
    results: list[tuple[str, str]] = []

    try:
        from plugins.free_uplift.policy import FreeUpliftPlugin
        p = FreeUpliftPlugin()
        if p.should_register(env):
            if conductor is not None and hasattr(conductor, "register_post_verify_hook"):
                conductor.register_post_verify_hook(p.post_verify_hook)
            results.append(("free_uplift", "registered"))
        else:
            results.append(("free_uplift",
                            "not registered (disabled / key absent / deprecated / no RPD headroom)"))
    except Exception as e:                    # a broken plugin must never break hm up
        results.append(("free_uplift", f"load error: {type(e).__name__}: {e}"))

    return results


def main() -> int:
    for name, status in load_plugins():
        print(f"  • plugin {name}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
