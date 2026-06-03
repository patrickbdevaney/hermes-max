# CLAUDE_research_fix_and_audit.md — Fix research MCP, then production audit

## What this directive does
1. Build a concise map of the essential code files (repomap-style, one MD)
2. Read it, diagnose and fix the research MCP failure
3. From the same map, audit every essential file for production-readiness

Do NOT rewrite working code. Fix bugs, report gaps, leave everything else alone.

---

## STEP 1 — build the concise code map

Write a single file: `/tmp/hm_codemap.md`

For each discovered essential file, record:
- file path (actual, discovered)
- its role in one sentence
- every function / class / tool name it exports (names only, no bodies)
- line count and last-modified date (`wc -l` and `stat`)
- any obvious red flags visible from signatures alone (missing error handling,
  hardcoded values, TODO/FIXME/stub markers, bare `except`, `pass` bodies)

Read each file fully if under 300 lines. For files over 300 lines, read the first
80 lines, the last 20 lines, and grep for: `def `, `class `, `@app`, `tool`,
`except`, `pass`, `TODO`, `FIXME`, `stub`, `raise NotImplementedError`,
`localhost`, `127.0.0.1`, `hardcode`.

Discover the repo structure first — do not assume paths:

```bash
find ~/hermes-max -type f \( -name "*.py" -o -name "*.sh" -o -name "*.yaml" -o -name "*.yml" -o -name "*.md" \) \
  | grep -v __pycache__ | grep -v ".git" | grep -v node_modules | sort
```

From that listing, identify the essential files by role:
- the hm CLI entrypoint (shell script or Python that handles `hm run`, `hm dev`, etc.)
- the conductor / preplan entrypoint (what `hm run` calls before hermes)
- every MCP server (any `server.py` under an `mcp-*` directory, or equivalent)
- the core module of any MCP that has one (research_core.py, search_core.py, etc.)
- the inference fabric entry (lib/inference.py or equivalent routing layer)
- the web UI server (ui/server.py or equivalent)
- any skill files in ~/.hermes/skills/ or ~/hermes-max/skills/ that direct tool use
- any config file that sets ports, endpoints, or provider keys (config.py, .env.example)

For each file discovered in these roles, add it to the map. Do not hardcode expected
paths — derive them from what is actually in the repo. If a role has no file, note it
as MISSING.

When the map is written, read it back in full before proceeding to Step 2.

---

## STEP 2 — diagnose and fix the research MCP failure

The eval showed: `deep_research span=0, citation URL present=1` — the agent did not
call the research tool despite it being available and a citation appearing in the output.
This means the agent answered from its own knowledge instead of calling the tool.

Diagnose in this order, stopping at the first confirmed root cause:

1. **Tool registration**: is `deep_research` (or equivalent) listed in the MCP server's
   tool manifest? Run a direct health/list probe:
   ```bash
   curl -s http://localhost:9110/tools 2>/dev/null || \
   python3 -c "import sys; sys.path.insert(0,'mcp-research'); \
   from server import app; print([t.name for t in app.tools])" 2>/dev/null
   ```
   If the tool is not registered or the server is not running, that is the root cause.

2. **Skill prompt**: find the skill file that instructs the agent to call deep_research.
   Check `~/.hermes/skills/` and `~/hermes-max/skills/`. Read the relevant skill.
   Does it contain a clear, unconditional directive to call the tool for research tasks,
   or does it leave it to the agent's discretion? If discretionary, that is the root cause.

3. **Server health at eval time**: check `~/.hermes-max/logs/live.jsonl` for the research
   eval turn. Were there connection errors, timeouts, or empty responses from the MCP?

4. **Agent bypass**: did the agent produce a citation URL without calling the tool?
   That means it hallucinated or used internal knowledge. The fix is a stronger skill
   directive, not a code change.

Fix the confirmed root cause. If the skill prompt is the issue, rewrite it to be
unambiguous: "For ANY research question, ALWAYS call deep_research first. Do not answer
from memory. Do not skip this tool." If the server is not running or not registered,
fix the registration.

After the fix, run ONE verification turn:
```bash
mkdir -p /tmp/eval-research && cd /tmp/eval-research
hermes -z --yolo "Research 'what is a Merkle tree in cryptography' using your \
deep research tool. Give a short answer with one source URL citation. 1 loop, 2 sources."
```
Confirm `deep_research span >= 1` in `~/.hermes-max/logs/live.jsonl`.
Report PASS or FAIL with the span count.

---

## STEP 3 — production audit from the code map

Read `hm_codemap.md`. For each essential file, check:

**A. Fatal bugs (fix immediately)**
- uncaught exceptions that would crash an MCP server process
- bare `except: pass` that swallows real errors silently
- missing `await` on async calls (will silently return coroutine objects)
- hardcoded localhost/port values that differ from the actual running config
- tool functions that return `None` instead of a valid MCP response shape
- any function the eval battery calls that has a `pass` or `raise NotImplementedError` body

**B. Production gaps (report, fix if small)**
- MCP servers with no health endpoint (the eval and the conductor need `/health`)
- missing graceful degradation (a server that crashes instead of returning `unknown`)
- tool registration that happens at import time and would silently fail if a dependency
  is missing (wrap in try/except with a clear startup error)
- any inference call without a timeout (will hang the agent loop indefinitely)
- the cost ledger: is every cloud LLM call recorded, or are some paths unmetered?

**C. Skill prompt gaps (report)**
- any MCP whose tool is registered but has no corresponding skill directive telling
  the agent when to call it (discretionary = will be skipped)
- skill directives that say "consider using" instead of "always call" for enforced tools

For each finding, report:
- file and line number
- severity: FATAL / GAP / SKILL
- one-line description
- the fix (one line of code or one sentence for skill rewrites)

Fix all FATAL findings in place. Report GAPs and SKILL issues without fixing unless
the fix is a single obvious line.

---

## STEP 4 — final status report

Write a plain summary:
```
RESEARCH FIX: PASS/FAIL — root cause was X, fix was Y, span count after fix = N
FATAL bugs fixed: N (list file:line for each)
GAPS reported: N (list)
SKILL gaps reported: N (list)
FILES MISSING from expected paths: N (list)
OVERALL: production-ready / needs work
```

Print this to stdout and write it to `/tmp/hm_audit_report.md`.

---

## CONSTRAINTS
- Read actual file contents, do not infer from filenames
- Do not rewrite working code — surgical fixes only
- Do not restart MCP servers unless a fix requires it
- If a file is large (>500 lines), read the first 80 lines and the last 20,
  then grep for the specific patterns in the audit checklist
- Commit any fixes made with message: `fix: research MCP + production audit fixes`