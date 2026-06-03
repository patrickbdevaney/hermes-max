"""kani_verify.py — Part A Phase 2: bounded model checking for CRITICAL Rust modules.

Kani proves absence of panics / arithmetic overflow / UB and assertion-unreachability up
to a bound, with NO manual proof burden and a concrete counterexample to replay. We route
here ONLY critical, non-concurrent Rust (criticality.py decides; Kani has no concurrency
support — concurrent code goes to Loom/Shuttle in Phase 4). The cheap pool PROPOSES a
`#[kani::proof]` harness with `kani::any()` inputs; the solver ADJUDICATES.

Four-value mapping:
  VERIFICATION SUCCESSFUL → verified{property:"kani", method}
  VERIFICATION FAILED     → counterexample{input(concrete trace), trace}
  timeout / unwinding bound hit / kani absent → DEGRADE to proptest (unknown when proptest
  is unavailable — honest, never a false verified).

Never raises. cargo-kani is heavy and often absent; every absence/timeout degrades.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    import pool as _pool
except Exception:  # noqa: BLE001
    _pool = None  # type: ignore
try:
    import enhanced_verify as _ev
except Exception:  # noqa: BLE001
    _ev = None  # type: ignore

KANI_TIMEOUT = int(os.environ.get("KANI_TIMEOUT_S", "300"))


def kani_available() -> bool:
    """cargo-kani present (the subcommand binary or `cargo kani` resolvable)."""
    if shutil.which("cargo-kani") or shutil.which("kani"):
        return True
    cargo = shutil.which("cargo")
    if not cargo:
        return False
    try:
        r = subprocess.run([cargo, "kani", "--version"], capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _public_rust_fns(src: str) -> list[str]:
    return re.findall(r"pub\s+fn\s+([a-zA-Z_][a-zA-Z0-9_]*)", src)


def _default_harness(fns: list[str]) -> str:
    """Deterministic fallback harness: prove panic/overflow-freedom by calling each public
    fn with symbolic `kani::any()` integer args (Kani checks panics/overflow/UB by default,
    no explicit assertion needed). Covers the common case with no model."""
    blocks = []
    for fn in fns[:8]:
        blocks.append(
            f"#[cfg(kani)]\n#[kani::proof]\nfn kani_{fn}() {{\n"
            f"    let a: i32 = kani::any();\n    let b: i32 = kani::any();\n"
            f"    let _ = {fn}(a, b);  // Kani proves no panic/overflow/UB over all inputs\n}}\n")
    return "\n".join(blocks)


_HARNESS_SYS = (
    "Write Kani proof harnesses for the public functions of this Rust module. For each, a "
    "`#[cfg(kani)]\\n#[kani::proof]` fn that binds inputs with `kani::any()` (constrain with "
    "`kani::assume(..)` only where the contract requires it) and asserts the postcondition "
    "with `assert!(..)`. Prove panic/overflow-freedom + the contract. Output ONLY Rust in "
    "one ```rust block, no prose."
)


def _generate_harness(src: str, fns: list[str]) -> str:
    prompt = f"Rust module (public fns: {', '.join(fns)}):\n\n{src[:6000]}"
    gen = None
    if _pool and _pool.available():
        r = _pool.map_cheap([prompt], system=_HARNESS_SYS, temperature=0.1, max_tokens=2000)
        gen = r[0] if r else None
    elif _ev is not None:
        gen = _ev._llm(_HARNESS_SYS + "\n\n" + prompt, 2000)
    if gen:
        m = re.search(r"```(?:rust)?\s*(.*?)```", gen, re.DOTALL)
        code = (m.group(1) if m else gen).strip()
        if "kani::proof" in code:
            return code
    return _default_harness(fns)


def _parse_kani(out: str) -> dict[str, Any]:
    """Map cargo-kani stdout to a four-value result."""
    if re.search(r"VERIFICATION:?-?\s*SUCCESSFUL", out, re.I) or "successful" in out.lower() and "failed" not in out.lower():
        return {"result": "verified", "property": "kani (panic/overflow/UB + asserts)",
                "method": "kani bounded model checking"}
    if re.search(r"VERIFICATION:?-?\s*FAILED", out, re.I) or "Failed Checks" in out:
        # Kani prints a concrete counterexample trace; capture the salient lines.
        trace_lines = [ln for ln in out.splitlines()
                       if re.search(r"(Failed Checks|assertion|overflow|panic|value:|Counterexample)", ln, re.I)]
        return {"result": "counterexample", "input": (trace_lines[:1] or [None])[0],
                "trace": "\n".join(trace_lines[:20])[:1800], "method": "kani"}
    if re.search(r"unwinding|unwind bound|timed out|timeout", out, re.I):
        return {"result": "degrade", "reason": "kani unwinding bound / timeout"}
    return {"result": "unknown", "reason": "kani output not conclusive"}


def kani_verify(path: str, concurrent: bool = False) -> dict[str, Any]:
    """Run Kani on the critical Rust crate at `path`. Returns a four-value result, or a
    `degrade` directive (caller falls to proptest). No-op-safe: kani absent → degrade."""
    if concurrent:
        return {"result": "unknown", "reason": "concurrent code — Kani has no concurrency "
                "support; route to Loom/Shuttle (Phase 4)", "method": "none"}
    if not kani_available():
        return {"result": "degrade", "reason": "cargo-kani not installed", "method": "none"}
    p = Path(path)
    root = p if p.is_dir() else p.parent
    # find a .rs to harness (the file, or lib.rs/main.rs in a crate)
    src_file = p if p.is_file() and p.suffix == ".rs" else None
    if src_file is None:
        for cand in (root / "src" / "lib.rs", root / "src" / "main.rs", root / "lib.rs"):
            if cand.exists():
                src_file = cand
                break
    if src_file is None:
        return {"result": "degrade", "reason": "no Rust source found to harness"}
    src = src_file.read_text(errors="replace")
    fns = _public_rust_fns(src)
    if not fns:
        return {"result": "unknown", "reason": "no public Rust fns to prove", "method": "none"}
    harness = _generate_harness(src, fns)
    # append the harness (cfg(kani)-gated, so it never affects normal builds) and run
    appended = src + "\n\n// ── kani harness (auto, cfg(kani)-gated) ──\n" + harness
    backup = src_file.read_text()
    try:
        src_file.write_text(appended)
        cargo = shutil.which("cargo")
        r = subprocess.run([cargo, "kani"], cwd=str(root), capture_output=True, text=True,
                           timeout=KANI_TIMEOUT)
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        return {**_parse_kani(out), "harness_fns": fns[:8]}
    except subprocess.TimeoutExpired:
        return {"result": "degrade", "reason": f"kani timed out after {KANI_TIMEOUT}s"}
    except Exception as e:  # noqa: BLE001
        return {"result": "degrade", "reason": f"kani run error: {type(e).__name__}"}
    finally:
        try:
            src_file.write_text(backup)  # always restore the unharnessed source
        except OSError:
            pass
