"""Cross-platform secret store — the one place a provider key is written, and the
one place it is read back (only ever to inject into the agent subprocess or to run
a live connection probe). It is NEVER returned to the browser and NEVER logged.

Backend selection (best available wins), per CLAUDE_ui.md's secret-handling rules:
  * macOS        → the `security` CLI (login Keychain)
  * Linux        → the `secret-tool` CLI (libsecret / gnome-keyring Secret Service)
  * any platform → the `keyring` Python library, if importable
  * fallback     → a gitignored `.env` with chmod 600 — ONLY where no keychain
                   exists (headless Linux / WSL2). This is the spec's documented
                   fallback, and the active path on a box without a keychain CLI.

Secret discipline enforced here:
  * `set_secret` takes a value, writes it to the backend, and returns ONLY status
    (backend + env-var name) — never the value.
  * `has_secret` returns a bool; there is no public "get" that hands a secret out.
  * `_resolve` (private) reads a value back, used solely by `launch_env` (inject
    into the spawned agent's env) and the connection probe (talk to the provider).
  * Nothing here ever calls print/log on a value. Callers must not either.

Keys live under one service ("hermes-max"), accounted by the provider's
`api_key_env` name (e.g. DEEPSEEK_API_KEY) so it lines up with what lib.inference
already resolves from the environment.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from typing import Optional

SERVICE = "hermes-max"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── backend detection (cached for the process) ───────────────────────────────
_backend_cache: Optional[str] = None


def _have_keyring_lib() -> bool:
    try:
        import keyring  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def backend() -> str:
    """One of: 'macos' | 'libsecret' | 'keyring' | 'dotenv'. Cached."""
    global _backend_cache
    if _backend_cache is not None:
        return _backend_cache
    forced = os.environ.get("HMX_SECRETS_BACKEND")
    if forced in ("macos", "libsecret", "keyring", "dotenv"):
        _backend_cache = forced
        return forced
    if shutil.which("security") and os.uname().sysname == "Darwin":
        _backend_cache = "macos"
    elif shutil.which("secret-tool"):
        _backend_cache = "libsecret"
    elif _have_keyring_lib():
        _backend_cache = "keyring"
    else:
        _backend_cache = "dotenv"
    return _backend_cache


def backend_label() -> str:
    return {
        "macos": "macOS Keychain",
        "libsecret": "OS keychain (libsecret)",
        "keyring": "OS keychain (python-keyring)",
        "dotenv": ".env (chmod 600 fallback)",
    }[backend()]


def is_keychain() -> bool:
    return backend() != "dotenv"


# ── .env helpers (fallback backend + always consulted for presence) ───────────
def env_file() -> str:
    return os.environ.get("HMX_ENV_FILE") or os.path.join(_REPO_ROOT, ".env")


_DOTENV_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def _dotenv_value(env_var: str) -> Optional[str]:
    try:
        with open(env_file()) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = _DOTENV_RE.match(line)
                if m and m.group(1) == env_var:
                    val = re.sub(r"\s+#.*$", "", m.group(2)).strip().strip('"').strip("'")
                    return val or None
    except OSError:
        return None
    return None


def _ensure_gitignored() -> None:
    """Best-effort: guarantee the .env fallback is gitignored (spec requirement)."""
    gi = os.path.join(_REPO_ROOT, ".gitignore")
    try:
        existing = ""
        if os.path.exists(gi):
            with open(gi) as f:
                existing = f.read()
        if not re.search(r"(?m)^\.env\s*$", existing):
            with open(gi, "a") as f:
                f.write(("" if existing.endswith("\n") or not existing else "\n") + ".env\n")
    except OSError:
        pass


def _dotenv_upsert(env_var: str, value: str) -> None:
    """Insert/replace NAME=value in .env, preserving all other lines, then chmod 600.
    Written atomically (temp + replace) so a crash never leaves a half file."""
    path = env_file()
    lines: list[str] = []
    found = False
    try:
        with open(path) as f:
            for line in f:
                m = _DOTENV_RE.match(line.strip())
                if m and m.group(1) == env_var:
                    lines.append(f"{env_var}={value}\n")
                    found = True
                else:
                    lines.append(line if line.endswith("\n") else line + "\n")
    except OSError:
        pass
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{env_var}={value}\n")
    tmp = path + ".tmp"
    # Create the temp file with 0600 from the start (never a wider window).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.writelines(lines)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass
    _ensure_gitignored()


# ── keychain backend ops (CLI / lib) ──────────────────────────────────────────
def _kc_set(env_var: str, value: str) -> None:
    b = backend()
    if b == "macos":
        # -U updates if it exists; -w via arg avoids an interactive prompt.
        subprocess.run(["security", "add-generic-password", "-a", env_var,
                        "-s", SERVICE, "-w", value, "-U"],
                       check=True, capture_output=True)
    elif b == "libsecret":
        # secret-tool reads the secret from stdin (never argv → never in `ps`).
        subprocess.run(["secret-tool", "store", "--label", f"{SERVICE} {env_var}",
                        "service", SERVICE, "account", env_var],
                       input=value.encode(), check=True, capture_output=True)
    elif b == "keyring":
        import keyring
        keyring.set_password(SERVICE, env_var, value)


def _kc_get(env_var: str) -> Optional[str]:
    b = backend()
    try:
        if b == "macos":
            out = subprocess.run(["security", "find-generic-password", "-a", env_var,
                                  "-s", SERVICE, "-w"], capture_output=True)
            return out.stdout.decode().rstrip("\n") if out.returncode == 0 else None
        if b == "libsecret":
            out = subprocess.run(["secret-tool", "lookup", "service", SERVICE,
                                  "account", env_var], capture_output=True)
            return out.stdout.decode() if out.returncode == 0 and out.stdout else None
        if b == "keyring":
            import keyring
            return keyring.get_password(SERVICE, env_var)
    except Exception:  # noqa: BLE001
        return None
    return None


def _kc_delete(env_var: str) -> None:
    b = backend()
    try:
        if b == "macos":
            subprocess.run(["security", "delete-generic-password", "-a", env_var,
                            "-s", SERVICE], capture_output=True)
        elif b == "libsecret":
            subprocess.run(["secret-tool", "clear", "service", SERVICE,
                            "account", env_var], capture_output=True)
        elif b == "keyring":
            import keyring
            keyring.delete_password(SERVICE, env_var)
    except Exception:  # noqa: BLE001
        pass


# ── public API (status only out; values stay in) ─────────────────────────────
def set_secret(env_var: str, value: str) -> dict:
    """Store a secret. Returns ONLY {ok, backend, env_var} — never the value."""
    if not env_var or not value:
        return {"ok": False, "error": "missing env_var or value"}
    if backend() == "dotenv":
        _dotenv_upsert(env_var, value)
    else:
        _kc_set(env_var, value)
    return {"ok": True, "backend": backend(), "backend_label": backend_label(),
            "env_var": env_var}


def delete_secret(env_var: str) -> dict:
    if backend() == "dotenv":
        # Remove the line from .env (rewrite without it).
        path = env_file()
        try:
            with open(path) as f:
                kept = [ln for ln in f
                        if not (_DOTENV_RE.match(ln.strip())
                                and _DOTENV_RE.match(ln.strip()).group(1) == env_var)]
            fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.writelines(kept)
            os.replace(path + ".tmp", path)
        except OSError:
            pass
    else:
        _kc_delete(env_var)
    return {"ok": True, "env_var": env_var}


def set_plain_env(env_var: str, value: str) -> dict:
    """Write a NON-secret env var (e.g. VLLM_BASE_URL) to .env. Endpoint/profile
    config always lives in .env regardless of the secret backend — it's not a
    secret, but it must be where lib.inference + `hm` already read it."""
    _dotenv_upsert(env_var, value)
    return {"ok": True, "env_var": env_var}


def _resolve(env_var: str) -> Optional[str]:
    """PRIVATE: the actual secret value. Used ONLY by launch_env (inject into the
    agent subprocess) and the connection probe (call the provider). Never exposed."""
    live = os.environ.get(env_var)
    if live:
        return live
    dot = _dotenv_value(env_var)
    if dot:
        return dot
    return _kc_get(env_var)


def has_secret(env_var: str) -> bool:
    """True if the key is resolvable anywhere (live env, .env, or keychain)."""
    if not env_var:
        return False
    return _resolve(env_var) is not None


def launch_env(env_vars: list[str]) -> dict[str, str]:
    """Secrets to inject into a spawned agent's environment: keychain-held keys that
    aren't already visible via the live env or .env (the agent reads those itself).
    For the dotenv backend this is empty — the key is already on disk in .env."""
    out: dict[str, str] = {}
    if backend() == "dotenv":
        return out
    for ev in env_vars:
        if os.environ.get(ev) or _dotenv_value(ev):
            continue  # already visible to the agent
        v = _kc_get(ev)
        if v:
            out[ev] = v
    return out
