"""Localhost hardening for the UI backend.

Even though the server binds 127.0.0.1, a local web page (or a DNS-rebinding
attack against a browser pointed at the loopback) is a real threat class. So we
defend in depth, exactly as CLAUDE_ui.md requires:

  * bind 127.0.0.1 only (never 0.0.0.0)               — see __main__.py
  * a one-time LAUNCH TOKEN that `hm` embeds in the opened URL; every /api request
    must carry it (query `?token=` for GETs incl. EventSource, `X-HMX-Token`
    header for POSTs). Compared in constant time.
  * Origin/Host validation on every request — blocks DNS-rebinding and the
    0.0.0.0-day class: the Host header must be a loopback authority, and when an
    Origin is present (browser cross-origin/POST) it must be a loopback origin.
  * the token doubles as the CSRF synchronizer token on POSTs (it lives only in
    the page's JS memory, never in a cookie, so it cannot be replayed cross-site).

No secret/provider key ever passes through here.
"""
from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlsplit

# Hosts we accept in the Host header authority (port-agnostic match below).
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def new_token() -> str:
    """A fresh URL-safe launch token for this server process."""
    return secrets.token_urlsafe(24)


def token_ok(presented: str | None, expected: str) -> bool:
    """Constant-time token comparison; empty/None never matches."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(str(presented), str(expected))


def _authority_host(authority: str | None) -> str | None:
    """Strip the port from an authority (`127.0.0.1:8787` → `127.0.0.1`)."""
    if not authority:
        return None
    a = authority.strip()
    # IPv6 literal like [::1]:8787 — keep the bracketed host.
    if a.startswith("["):
        end = a.find("]")
        return a[: end + 1] if end != -1 else a
    return a.rsplit(":", 1)[0] if ":" in a else a


def host_ok(host_header: str | None) -> bool:
    """The Host header must name a loopback authority."""
    return _authority_host(host_header) in _LOOPBACK_HOSTS


def origin_ok(origin_header: str | None) -> bool:
    """An absent Origin is allowed (curl / same-origin GETs don't always send it);
    a present Origin must be a loopback origin (blocks cross-site POSTs)."""
    if not origin_header or origin_header == "null":
        return True
    try:
        host = urlsplit(origin_header).hostname
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}
