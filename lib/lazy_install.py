"""In-server lazy-install guard — the Python sibling of lib/ensure_dep.sh.

A server calls ``ensure("crawl4ai")`` (at import time or first use); if the dep
is missing from its OWN venv, this pip-installs into ``sys.executable``'s
environment and retries the import, so a partially set-up machine self-heals on
first call instead of crashing. Mirrors Hermes's native lazy-install (``ddgs``).

Returns the imported module, or ``None`` if it could not be provided — the
caller is expected to DEGRADE GRACEFULLY (e.g. fall back to a simpler extractor)
rather than crash. Never raises for a missing optional dependency.

Usage::

    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
    from lazy_install import ensure
    crawl4ai = ensure("crawl4ai")           # import name == pip spec
    trafilatura = ensure("trafilatura")
    sbert = ensure("sentence_transformers", "sentence-transformers")  # differ
    if crawl4ai is None:
        ...  # degrade
"""
from __future__ import annotations

import importlib
import subprocess
import sys


def ensure(import_name: str, pip_spec: str | None = None, *, quiet: bool = True):
    """Import ``import_name``, lazily pip-installing ``pip_spec`` if absent.

    Returns the module on success, or ``None`` if it cannot be provided.
    """
    try:
        return importlib.import_module(import_name)
    except Exception:
        pass

    spec = pip_spec or import_name
    cmd = [sys.executable, "-m", "pip", "install", spec]
    if quiet:
        cmd.insert(4, "-q")
    try:
        subprocess.check_call(
            cmd,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.STDOUT if quiet else None,
        )
    except Exception as e:  # noqa: BLE001 — install failure must not crash the server
        sys.stderr.write(f"[lazy_install] install of {spec!r} failed: {e}\n")
        return None

    try:
        return importlib.import_module(import_name)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[lazy_install] {import_name!r} still unimportable after install: {e}\n")
        return None
