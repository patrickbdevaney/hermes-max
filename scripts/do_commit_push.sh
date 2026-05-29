#!/usr/bin/env bash
# Harden .gitignore, stage with a safety gate (abort if any secret/venv/db/cache
# is staged), then commit + push. Idempotent on the .gitignore additions.
set -uo pipefail
cd /home/patrickd/hermes-max

echo "===== 1. harden root .gitignore (add only missing entries) ====="
add_ignore() {
  local pat="$1"
  if ! grep -qxF "$pat" .gitignore 2>/dev/null; then
    printf '%s\n' "$pat" >> .gitignore
    echo "  + $pat"
  fi
}
# ensure trailing newline first
[ -n "$(tail -c1 .gitignore 2>/dev/null)" ] && printf '\n' >> .gitignore
for p in ".venv/" "venv/" "*.pyc" "*.pyo" "*.db" "*.db-wal" "*.db-shm" \
         ".pytest_cache/" "*.egg-info/" "dist/" "build/" "node_modules/" \
         ".DS_Store" "*.log"; do
  add_ignore "$p"
done
echo "--- final .gitignore ---"; cat .gitignore

echo; echo "===== 2. stage ====="
git add -A

echo; echo "===== 3. SAFETY GATE — staged files that must NEVER be committed ====="
DANGER="$(git diff --cached --name-only | grep -nE '(^|/)\.env$|\.venv/|(^|/)venv/|\.pyc$|\.db($|-)|node_modules/|\.egg-info/' || true)"
if [ -n "$DANGER" ]; then
  echo "ABORT: dangerous paths staged:"; echo "$DANGER"
  echo "Unstaging and stopping — fix .gitignore."
  git reset -q
  exit 2
fi
echo "  clean — no secrets/venvs/dbs/caches staged."

echo; echo "===== 4. staged file count + top-level summary ====="
echo "  total staged: $(git diff --cached --name-only | wc -l) files"
git diff --cached --name-only | sed 's#/.*##' | sort | uniq -c | sort -rn

echo; echo "===== 5. confirm .env ignored, .env.example tracked ====="
git check-ignore .env >/dev/null && echo "  .env IGNORED (good)" || echo "  WARN: .env not ignored"
git diff --cached --name-only | grep -qx '.env.example' && echo "  .env.example staged (good)" || echo "  note: .env.example not staged"

echo; echo "===== 6. commit ====="
git config user.email >/dev/null 2>&1 || git config user.email "@gmail.com"
git config user.name  >/dev/null 2>&1 || git config user.name  ""
git commit -q -F - <<'MSG'
Finalize hermes-max harness: config verification, scoped checkpoint .gitignore fix, and V1/V2/V3 validation

- FIX 1/2: confirmed compression (threshold 0.75, target_ratio 0.35, protect_last_n 40,
  protect_first_n 5) and tool_use_enforcement=required in ~/.hermes/config.yaml (no change needed).
- FIX 3: mcp-checkpoint now writes a sensible default .gitignore before `git add -A` when a repo
  has none (respects an existing one), so caches/secrets/build artifacts never enter a checkpoint;
  smoke_test.py asserts a created __pycache__/x.pyc is not in `git ls-files`.
- FIX 4: honest BM25-only RAG path — healthcheck.sh prints a clear "RAG: BM25-only" banner and the
  README documents enabling semantic RAG via EMBED_BASE_URL (no faked embeddings).
- Add scripts/finalize_fixes.py and scripts/finalize_validation.py: the latter drives the REAL
  mcp-verify + mcp-checkpoint + mcp-codebase-rag over MCP for V1 (planned multi-file FastAPI
  task-tracker), V2 (compounding via rag reuse) and V3 (stuck-reset). 21/21 checks pass.

Model endpoint is always read from $VLLM_BASE_URL; no host hardcoded.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
MSG
echo "  commit: $(git --no-pager log --oneline -1)"

echo; echo "===== 7. push ====="
git push -u origin main 2>&1
echo "PUSH_EXIT=$?"
echo; echo "===== DONE ====="
git --no-pager log --oneline -1
