#!/usr/bin/env bash
# Self-hosted SearXNG — the sovereign search backend (Hermes web.backend=searxng,
# and mcp-docs search_docs). Fire-and-forget container on :8080.
#
# IMPORTANT: SearXNG ships with formats:[html] only, so the JSON API returns 403.
# mcp-docs (and any programmatic caller) needs JSON, so this script enables
# json/csv in the container's settings.yml and restarts — idempotently.
set -uo pipefail

if ! docker ps -a --format '{{.Names}}' | grep -qx searxng; then
  echo "→ starting SearXNG container on :8080"
  docker run -d --name searxng \
    -p 8080:8080 \
    --restart unless-stopped \
    searxng/searxng:latest
else
  echo "→ SearXNG container already exists"
  docker start searxng >/dev/null 2>&1 || true
fi

echo "→ ensuring JSON output is enabled (settings.yml formats: html, json, csv)"
docker exec -u 0 searxng python3 - <<'PY' 2>/dev/null || echo "  (could not edit settings yet — container may still be initialising; re-run this script)"
import re
p = "/etc/searxng/settings.yml"
s = open(p).read()
m = re.search(r"(?m)^(\s*)formats:\s*\n((?:\1\s+-\s*\w+\s*\n)+)", s)
if m and "json" not in m.group(2):
    ind = m.group(1)
    s = s[:m.start()] + f"{ind}formats:\n{ind}  - html\n{ind}  - json\n{ind}  - csv\n" + s[m.end():]
    open(p, "w").write(s)
    print("  enabled json/csv")
else:
    print("  json already enabled" if m else "  formats block not found (unexpected)")
PY
docker restart searxng >/dev/null 2>&1 && echo "  restarted; JSON API live at http://localhost:8080/search?q=...&format=json"
