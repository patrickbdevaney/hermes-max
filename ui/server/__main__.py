"""Bootstrap the UI server. Run via `python3 -m ui.server` (the `hm ui` verb does
exactly this).

    python3 -m ui.server [--port N] [--no-open] [--token T]

Friction-free by default: binds 127.0.0.1, opens your browser, and prints ONE line
— `hermes-max UI  →  http://localhost:7080`. No token needed to open the page; the
localhost hardening (loopback bind + Origin/Host validation + a SameSite=Strict
CSRF cookie) is what protects it. The port is sticky: it persists to
~/.hermes-max/ui.conf so the address is always the same; if it's taken we pick the
next free one and print it.

`--token T` is OPT-IN: it adds a required bearer token (carried in the opened URL)
for operators who expose the UI beyond loopback (e.g. over Tailscale)."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

# Put the repo root on sys.path so `import lib...` works no matter the CWD. This
# file is at <repo>/ui/server/__main__.py → repo root is three parents up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from ui.server import security  # noqa: E402
from ui.server.app import Handler  # noqa: E402

DEFAULT_PORT = 7080


def _config_dir() -> str:
    return os.path.expanduser(os.environ.get("HERMES_MAX_CONFIG_DIR") or "~/.hermes-max")


def _ui_conf_path() -> str:
    return os.path.join(_config_dir(), "ui.conf")


def _load_saved_port() -> int | None:
    try:
        with open(_ui_conf_path()) as f:
            return int(json.load(f).get("port"))
    except (OSError, ValueError, TypeError):
        return None


def _save_port(port: int) -> None:
    try:
        os.makedirs(_config_dir(), exist_ok=True)
        with open(_ui_conf_path(), "w") as f:
            json.dump({"port": port}, f)
    except OSError:
        pass


def _bind(host: str, port: int) -> ThreadingHTTPServer | None:
    """Try to bind; return the server or None if the port is taken."""
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
        httpd.daemon_threads = True
        return httpd
    except OSError:
        return None


def _open_browser(url: str) -> None:
    """Open the default browser without ever blocking. xdg-open (Linux) / open
    (macOS); on failure (headless/SSH) we just don't — the URL was already printed."""
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    except (OSError, ValueError):
        pass  # headless / no opener — the printed URL is the fallback


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="hm ui", description="hermes-max web UI")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (forced to loopback unless --token is set)")
    ap.add_argument("--port", type=int, default=None,
                    help=f"port (default: sticky, from ui.conf, else {DEFAULT_PORT})")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser")
    ap.add_argument("--token", nargs="?", const="__auto__", default=None,
                    help="opt-in bearer token for remote exposure (Tailscale); "
                         "bare --token mints one, --token VALUE uses yours")
    args = ap.parse_args(argv)

    # Token is OPT-IN. Default (no flag) → no token, loopback-only hardening.
    if args.token is None:
        launch_token = os.environ.get("HMX_UI_TOKEN", "")
    else:
        launch_token = security.new_token() if args.token == "__auto__" else args.token

    # With a token the operator may expose beyond loopback (e.g. bind a Tailscale
    # IP); without one we hard-refuse anything but loopback.
    host = args.host
    if not launch_token and host not in ("127.0.0.1", "localhost", "::1"):
        print(f"✗ refusing to bind non-loopback {host!r} without --token; using 127.0.0.1",
              file=sys.stderr)
        host = "127.0.0.1"

    Handler.launch_token = launch_token
    Handler.csrf_token = security.new_token()  # always — the SameSite CSRF cookie

    # Sticky port: explicit --port > saved ui.conf > DEFAULT_PORT. If taken, walk up.
    start_port = args.port or _load_saved_port() or DEFAULT_PORT
    httpd = None
    port = start_port
    for cand in range(start_port, start_port + 50):
        httpd = _bind(host, cand)
        if httpd is not None:
            port = cand
            break
    if httpd is None:
        print(f"✗ could not find a free port near {start_port}", file=sys.stderr)
        return 1
    _save_port(port)

    # Quiet by design: ONE line. (A non-default port is still just this line, with
    # the real port — so the address is never a surprise.)
    display_host = "localhost" if host in ("127.0.0.1", "::1") else host
    suffix = f"/?token={launch_token}" if launch_token else ""
    url = f"http://{display_host}:{port}{suffix}"
    # Default flow: ONE clean line, no token. Opt-in --token (remote) flow: include
    # the token in the printed URL so it's actually openable from another machine.
    print(f"hermes-max UI  →  {url}", flush=True)

    if not args.no_open and not os.environ.get("HMX_UI_NO_OPEN"):
        threading.Thread(target=lambda: _open_browser(url), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
