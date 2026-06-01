#!/usr/bin/env bash
# install.sh — friendly first-run front door for hermes-max.
#
# Does the SAFE, deterministic setup and hands off the parts that need human
# judgment. It will NOT install Hermes for you, guess your GPU/endpoint, or write
# any API keys — those are yours to set. The heavy, idempotent build (venvs, deps,
# MCP registration) is delegated to the existing engine, bootstrap.sh.
#
#   ./install.sh            # set up config + run the idempotent build
#   ./install.sh --check    # dry-run audit only (no build, nothing changed)
#   ./install.sh --no-build # config + PATH only; skip the venv/dep build
#
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_ROOT}"

CHECK=0; NO_BUILD=0
for a in "$@"; do case "${a}" in
  --check)    CHECK=1 ;;
  --no-build) NO_BUILD=1 ;;
esac; done

say()  { printf '%s\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ⚠ %s\n' "$*"; }
miss() { printf '  ✗ %s\n' "$*"; }

say "═══ hermes-max install ═══"

# ── 1. prerequisites (report; never auto-install) ────────────────────────────
say "Prerequisites:"
if command -v python3 >/dev/null 2>&1; then
  ok "python3 — $(python3 -V 2>&1)"
  python3 - <<'PY' 2>/dev/null || warn "python 3.10+ recommended"
import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)
PY
else
  miss "python3 not found — install Python 3.10+"
fi
command -v git    >/dev/null 2>&1 && ok "git present"          || miss "git not found"
command -v docker >/dev/null 2>&1 && ok "docker present (optional: SearXNG/Crawl4AI/Phoenix)" \
                                   || warn "docker not found — optional; the doc/research containers won't run, lean fallbacks apply"
command -v tmux   >/dev/null 2>&1 && ok "tmux present (for the hm dev cockpit)" \
                                   || warn "tmux not found — optional; hm dev prints manual steps without it"
if command -v hermes >/dev/null 2>&1; then
  ok "hermes on PATH"
else
  warn "hermes not on PATH — install the Hermes agent first: github.com/nousresearch/hermes-agent"
  warn "  (hermes-max wraps it; it is not bundled. The stack still installs; you'll need hermes to run tasks.)"
fi

# ── 2. .env (copy template if absent; never write keys) ──────────────────────
say "Config:"
if [ -f "${REPO_ROOT}/.env" ]; then
  ok ".env already exists — left untouched"
elif [ "${CHECK}" = "1" ]; then
  warn ".env absent — would copy from .env.example (skipped: --check)"
else
  cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
  ok ".env created from .env.example — now edit it (see the MINIMUM VIABLE = 2 LINES header)"
fi

# ── 3. config trinity → ~/.hermes-max/ (delegated to scripts/setup.sh) ───────
if [ "${CHECK}" = "1" ]; then
  warn "would copy config/{inference,roles,modes,conductor}.example.yaml → \$HERMES_MAX_CONFIG_DIR (skipped: --check)"
elif [ -f "${REPO_ROOT}/scripts/setup.sh" ]; then
  bash "${REPO_ROOT}/scripts/setup.sh" >/dev/null 2>&1 \
    && ok "config trinity present in ~/.hermes-max/ (edit those copies, not the code)" \
    || warn "scripts/setup.sh reported an issue — re-run it directly to see why"
fi

# ── 4. hm onto PATH ──────────────────────────────────────────────────────────
chmod +x "${REPO_ROOT}/hm" 2>/dev/null || true
if [ "${CHECK}" = "1" ]; then
  warn "would symlink hm → ~/.local/bin/hm (skipped: --check)"
else
  bash "${REPO_ROOT}/hm" install 2>/dev/null | sed 's/^/  /' || warn "run ./hm install to put hm on PATH"
fi

# ── 5. the heavy, idempotent build (delegated to bootstrap.sh) ───────────────
if [ "${CHECK}" = "1" ]; then
  say "Build (dry-run audit):"
  [ -f "${REPO_ROOT}/bootstrap.sh" ] && bash "${REPO_ROOT}/bootstrap.sh" --check || warn "bootstrap.sh not found"
elif [ "${NO_BUILD}" = "1" ]; then
  warn "skipping the venv/dep/MCP build (--no-build). Run ./bootstrap.sh when ready."
elif [ -f "${REPO_ROOT}/bootstrap.sh" ]; then
  say "Build (venvs, deps, MCP registration — idempotent):"
  bash "${REPO_ROOT}/bootstrap.sh" || warn "bootstrap.sh exited non-zero — re-run ./bootstrap.sh --check to audit"
else
  warn "bootstrap.sh not found — cannot build the stack"
fi

# ── 6. the next step is always obvious ───────────────────────────────────────
cat <<'EOF'

═══ next: edit .env, then start a profile ═══

  ── Profile A — you own a GPU (DGX / Thor / RTX / Mac Studio) ──
     set in .env:  VLLM_BASE_URL=http://<endpoint>:8001/v1   OPENROUTER_API_KEY=...
     hm up --free                 # local drives, free planner
     hm up --free --free-uplift   # + per-file coherence check (after a $10 OpenRouter deposit)

  ── Profile B — no GPU (laptop / mini-pc / vps) ──
     set in .env:  DEEPINFRA_API_KEY=...   (or DEEPSEEK_API_KEY=...)
     hm up --full                 # economic API drives + plans, no rate limits

  then:  hermes        # launch the agent
         hm dev         # the one-window cockpit
         hm status      # what's running + mode + today's spend

  Docs: README.md · QUICKSTART.md · docs/profiles.md
EOF
