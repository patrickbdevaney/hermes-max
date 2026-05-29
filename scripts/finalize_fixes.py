#!/usr/bin/env python3
"""Finalize fixes for the hermes-max harness (FIX 1-4 from CLAUDE_finalize.md).

Safe + idempotent + self-inspecting. Backs up ~/.hermes/config.yaml before any
edit, only touches the four compression keys, validates YAML parses afterward
(restores the backup if it would corrupt), patches the checkpoint .gitignore
mechanism + its smoke test, adds the honest BM25 banner to the rag healthcheck,
and runs both smoke tests. Writes a full markdown report to REPORT_PATH and
mirrors everything to stdout. Never touches the model host.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
CONFIG = os.path.join(HOME, ".hermes", "config.yaml")
REPO = "/home/patrickd/hermes-max"
CKPT_CORE = os.path.join(REPO, "mcp-checkpoint", "checkpoint_core.py")
CKPT_SMOKE = os.path.join(REPO, "mcp-checkpoint", "smoke_test.py")
RAG_HC = os.path.join(REPO, "mcp-codebase-rag", "healthcheck.sh")
RAG_README = os.path.join(REPO, "mcp-codebase-rag", "README.md")
REPORT_PATH = "/tmp/hermes_finalize_report.md"

_report: list[str] = []


def say(msg: str = "") -> None:
    print(msg, flush=True)
    _report.append(msg)


def section(title: str) -> None:
    say("\n" + "=" * 78)
    say(title)
    say("=" * 78)


# ─────────────────────────────────────────────────────────────────────────────
# FIX 1 + FIX 2 — config.yaml
# ─────────────────────────────────────────────────────────────────────────────
WANT = {"threshold": "0.75", "target_ratio": "0.35", "protect_last_n": "40", "protect_first_n": "5"}


def _compression_block(src: str):
    """Return (match, block_text). Block = from '^compression:' up to next
    top-level key (a line starting non-whitespace) or EOF."""
    m = re.search(r"(?m)^compression:[ \t]*\n(?:[ \t].*\n?|\n)*", src)
    return m


def fix_config() -> None:
    section("FIX 1 + FIX 2 — ~/.hermes/config.yaml")
    if not os.path.exists(CONFIG):
        say(f"FAIL: config not found at {CONFIG}")
        return
    src = open(CONFIG).read()

    m = _compression_block(src)
    if not m:
        say("FAIL: no `compression:` block found in config.")
        return
    block = m.group(0)
    say("BEFORE (compression block):")
    for ln in block.rstrip("\n").splitlines():
        say("    " + ln)

    # tool_use_enforcement (report only — FIX 2)
    enf = [ln.strip() for ln in src.splitlines() if "tool_use_enforcement" in ln]
    say("")
    say(f"FIX 2 tool_use_enforcement (report-only, NOT changed): {enf or '(not present)'}")

    # Edit each key within the block only.
    new_block = block
    changed = []
    for key, val in WANT.items():
        # match 'key: <something>' at any indent within the block
        pat = re.compile(rf"(?m)^([ \t]+){re.escape(key)}:[ \t]*[^\n#]*")
        if pat.search(new_block):
            def _repl(mm, _v=val):
                return f"{mm.group(1)}{key}: {_v}"
            before = new_block
            new_block = pat.sub(_repl, new_block, count=1)
            if new_block != before:
                changed.append(key)
        else:
            # insert under compression: with 2-space indent
            new_block = re.sub(r"(?m)^(compression:[ \t]*\n)", rf"\1  {key}: {val}\n", new_block, count=1)
            changed.append(key + " (inserted)")

    if new_block == block:
        say("\nAll four compression keys already at target values — no change needed.")
    else:
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = f"{CONFIG}.finalize.bak.{ts}"
        with open(bak, "w") as f:
            f.write(src)
        say(f"\nBacked up config -> {bak}")
        new_src = src[: m.start()] + new_block + src[m.end():]
        # validate YAML parses before writing
        ok = True
        try:
            import yaml  # type: ignore

            yaml.safe_load(new_src)
        except ModuleNotFoundError:
            say("(PyYAML not available — skipping parse validation; edit is regex-scoped & reversible via backup)")
        except Exception as e:  # noqa: BLE001
            ok = False
            say(f"FAIL: edited config does NOT parse as YAML ({e}); NOT writing. Backup preserved.")
        if ok:
            with open(CONFIG, "w") as f:
                f.write(new_src)
            say(f"Applied changes to keys: {changed}")

    # re-read & show after
    after_src = open(CONFIG).read()
    am = _compression_block(after_src)
    say("\nAFTER (compression block):")
    for ln in am.group(0).rstrip("\n").splitlines():
        say("    " + ln)


# ─────────────────────────────────────────────────────────────────────────────
# FIX 3 — checkpoint .gitignore mechanism + smoke-test assertion
# ─────────────────────────────────────────────────────────────────────────────
GITIGNORE_CONST = '''
# A sensible default .gitignore, written into a project repo at checkpoint time
# only when it has none — so `git add -A` never sweeps caches, virtualenvs,
# secrets, or build artifacts into a "last green" checkpoint. An existing
# .gitignore is always respected (never overwritten).
_DEFAULT_GITIGNORE = """\
__pycache__/
*.pyc
.venv/
venv/
node_modules/
.env
.env.*
*.log
.DS_Store
dist/
build/
*.egg-info/
"""


def _ensure_gitignore(repo: str) -> bool:
    """Write the default .gitignore IF the repo has none. Returns True if it
    created one; False if one already existed (respected) or on any error."""
    path = os.path.join(repo, ".gitignore")
    if os.path.exists(path):
        return False
    try:
        with open(path, "w") as f:
            f.write(_DEFAULT_GITIGNORE)
        return True
    except OSError:
        return False

'''


def fix_checkpoint_core() -> None:
    section("FIX 3a — mcp-checkpoint/checkpoint_core.py (.gitignore before git add -A)")
    src = open(CKPT_CORE).read()
    if "_ensure_gitignore" in src:
        say("Already patched (_ensure_gitignore present) — idempotent no-op.")
    else:
        # Insert the constant + helper right before the public tools banner.
        anchor = "# ── public tools ─"
        if anchor not in src:
            say("FAIL: could not find public-tools anchor in checkpoint_core.py")
            return
        src = src.replace(anchor, GITIGNORE_CONST.lstrip("\n") + "\n" + anchor, 1)
        # Call it before `git add -A` inside checkpoint().
        add_line = '    add = _git(["add", "-A"], repo)'
        call = (
            "    # FIX 3: ensure caches/secrets/build artifacts are filtered before add -A.\n"
            "    created_gitignore = _ensure_gitignore(repo)\n"
        )
        if add_line not in src:
            say("FAIL: could not find `git add -A` line in checkpoint().")
            return
        src = src.replace(add_line, call + add_line, 1)
        # Surface in the success return so callers can see it.
        src = src.replace(
            '            "verified": verified,\n            "warnings": warnings,\n        }',
            '            "verified": verified,\n            "gitignore_created": created_gitignore,\n            "warnings": warnings,\n        }',
            1,
        )
        with open(CKPT_CORE, "w") as f:
            f.write(src)
        say("Patched: added _DEFAULT_GITIGNORE + _ensure_gitignore(), called before `git add -A`.")

    r = subprocess.run([sys.executable, "-m", "py_compile", CKPT_CORE], capture_output=True, text=True)
    say(f"py_compile checkpoint_core.py: {'OK' if r.returncode == 0 else 'FAIL ' + r.stderr}")


PYC_CHECK = '''
        # 1b. FIX 3: caches/build artifacts must be IGNORED, never checkpointed.
        cache = Path(tmpdir) / "__pycache__"
        cache.mkdir(exist_ok=True)
        (cache / "x.pyc").write_text("junk-bytecode")
        checkpoint_core.checkpoint("after pyc", verify=True, repo_path=tmpdir)
        tracked = _git(["ls-files"], tmpdir).stdout.split()
        if any(t.endswith(".pyc") or t.startswith("__pycache__/") for t in tracked):
            _fail(f"FIX 3 broken: __pycache__/.pyc leaked into checkpoint: {tracked}")
        if ".gitignore" not in tracked:
            _fail(f"FIX 3 broken: .gitignore was not created/tracked: {tracked}")
        _ok("FIX 3: __pycache__/x.pyc ignored (not in git ls-files); .gitignore present")
'''


def fix_checkpoint_smoke() -> None:
    section("FIX 3b — mcp-checkpoint/smoke_test.py (assert .pyc is ignored)")
    src = open(CKPT_SMOKE).read()
    if "FIX 3:" in src and ".pyc leaked" in src:
        say("Already patched (pyc-ignored assertion present) — idempotent no-op.")
    else:
        anchor = '        _ok(f"green checkpoint committed verified: {green_sha[:12]}")\n'
        if anchor not in src:
            say("FAIL: could not find green-checkpoint anchor in smoke_test.py")
            return
        src = src.replace(anchor, anchor + PYC_CHECK, 1)
        with open(CKPT_SMOKE, "w") as f:
            f.write(src)
        say("Patched: inserted __pycache__/x.pyc ignore assertion after the green checkpoint.")
    r = subprocess.run([sys.executable, "-m", "py_compile", CKPT_SMOKE], capture_output=True, text=True)
    say(f"py_compile smoke_test.py: {'OK' if r.returncode == 0 else 'FAIL ' + r.stderr}")


def run_checkpoint_smoke() -> None:
    section("FIX 3c — run mcp-checkpoint smoke test (real verify boundary, local)")
    venv_py = os.path.join(REPO, "mcp-checkpoint", ".venv", "bin", "python")
    py = venv_py if os.path.exists(venv_py) else sys.executable
    say(f"python: {py}")
    r = subprocess.run(
        [py, CKPT_SMOKE], cwd=os.path.join(REPO, "mcp-checkpoint"),
        capture_output=True, text=True, timeout=300,
    )
    say(r.stdout[-4000:])
    if r.stderr.strip():
        say("STDERR:\n" + r.stderr[-2000:])
    say(f"checkpoint smoke test exit={r.returncode} -> {'PASS' if r.returncode == 0 else 'FAIL'}")

    # ruff lint (no regression) if available
    ruff = os.path.join(REPO, "mcp-checkpoint", ".venv", "bin", "ruff")
    if os.path.exists(ruff):
        rr = subprocess.run([ruff, "check", CKPT_CORE, CKPT_SMOKE], capture_output=True, text=True)
        say(f"ruff check: {'clean' if rr.returncode == 0 else rr.stdout + rr.stderr}")
    else:
        say("ruff not in checkpoint venv — skipped lint.")


# ─────────────────────────────────────────────────────────────────────────────
# FIX 4 — honest BM25 banner + README note (host down => path B)
# ─────────────────────────────────────────────────────────────────────────────
BANNER_MARKER = "# FIX 4: RAG semantic-vs-BM25 honesty banner"
BANNER = f'''
{BANNER_MARKER}
_ebu="${{EMBED_BASE_URL:-}}"
if [ -z "$_ebu" ] && [ -f "$(dirname "$0")/../.env" ]; then
  _ebu="$(grep -E '^EMBED_BASE_URL=' "$(dirname "$0")/../.env" | tail -1 | cut -d= -f2-)"
fi
if [ -z "$_ebu" ]; then
  echo "RAG: BM25-only (no EMBED_BASE_URL set — semantic retrieval disabled)"
fi
'''


def fix_rag_healthcheck() -> None:
    section("FIX 4 — mcp-codebase-rag/healthcheck.sh honest BM25 banner")
    say("CURRENT healthcheck.sh:")
    cur = open(RAG_HC).read()
    for ln in cur.splitlines():
        say("    " + ln)
    if BANNER_MARKER in cur:
        say("\nBanner already present — idempotent no-op.")
    else:
        with open(RAG_HC, "a") as f:
            if not cur.endswith("\n"):
                f.write("\n")
            f.write(BANNER)
        say("\nAppended honest BM25-only banner (prints when EMBED_BASE_URL is empty).")
    # show EMBED_BASE_URL value from .env
    env_path = os.path.join(REPO, ".env")
    ebu = ""
    if os.path.exists(env_path):
        for ln in open(env_path):
            if ln.startswith("EMBED_BASE_URL="):
                ebu = ln.split("=", 1)[1].strip()
    say(f"EMBED_BASE_URL in .env = {ebu!r}  -> path {'A (semantic)' if ebu else 'B (honest BM25-only)'}")


README_NOTE = """

## Enabling semantic (hybrid) RAG later

This server runs **BM25-only** whenever `EMBED_BASE_URL` is empty (the honest
default — the chat vLLM does not serve `/embeddings`). Retrieval still works; it
is lexical rather than semantic. `healthcheck.sh` prints a clear
`RAG: BM25-only (...)` banner in this mode so the degradation is never silent.

To enable hybrid (BM25 + dense) retrieval:

1. Serve an OpenAI-compatible embedding model (e.g. a second vLLM, or a small
   local embed server) reachable over the network.
2. Set in `~/hermes-max/.env` (and `.env.example`):
   ```
   EMBED_BASE_URL=http://<host>:<port>/v1
   EMBED_MODEL=<model-id-or-/model>
   ```
3. Restart `mcp-codebase-rag` and re-index. The healthcheck banner disappears
   and queries become hybrid. No code change is required — the switch is the
   single `EMBED_BASE_URL` variable.
"""


def fix_rag_readme() -> None:
    section("FIX 4 — mcp-codebase-rag/README.md note on enabling semantic RAG")
    cur = open(RAG_README).read()
    if "Enabling semantic (hybrid) RAG later" in cur:
        say("README note already present — idempotent no-op.")
        return
    with open(RAG_README, "a") as f:
        if not cur.endswith("\n"):
            f.write("\n")
        f.write(README_NOTE)
    say("Appended 'Enabling semantic RAG later' section to README.")


def run_rag_smoke() -> None:
    section("FIX 4 — run mcp-codebase-rag smoke test (BM25 path)")
    smoke = os.path.join(REPO, "mcp-codebase-rag", "smoke_test.py")
    venv_py = os.path.join(REPO, "mcp-codebase-rag", ".venv", "bin", "python")
    py = venv_py if os.path.exists(venv_py) else sys.executable
    env = dict(os.environ)
    env.pop("EMBED_BASE_URL", None)  # force BM25 path B
    try:
        r = subprocess.run(
            [py, smoke], cwd=os.path.join(REPO, "mcp-codebase-rag"),
            capture_output=True, text=True, timeout=300, env=env,
        )
        say(r.stdout[-4000:])
        if r.stderr.strip():
            say("STDERR:\n" + r.stderr[-2000:])
        say(f"rag smoke test exit={r.returncode} -> {'PASS' if r.returncode == 0 else 'FAIL'}")
    except subprocess.TimeoutExpired:
        say("rag smoke test TIMED OUT (>300s) — likely tried to reach a network embed endpoint.")


def main() -> None:
    say(f"# hermes-max finalize fixes — {_dt.datetime.now().isoformat()}")
    fix_config()
    fix_checkpoint_core()
    fix_checkpoint_smoke()
    run_checkpoint_smoke()
    fix_rag_healthcheck()
    fix_rag_readme()
    run_rag_smoke()
    section("DONE")
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(_report))
    say(f"\nReport written to {REPORT_PATH}")


if __name__ == "__main__":
    try:
        main()
    finally:
        with open(REPORT_PATH, "w") as f:
            f.write("\n".join(_report))
