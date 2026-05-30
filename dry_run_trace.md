# hermes-max dry-run trace (real-inference smoke proof)

- **mode**: `full`  ·  **started**: 2026-05-30T09:28:50Z  ·  **wall**: 19.8s
- **endpoint** (`$VLLM_BASE_URL`): `http://YOUR_TAILSCALE_IP:8001/v1`
- **result**: 15 ✅ PASS · 0 ⊘ skip · 0 ❌ fail (of 15 components)
- **verdict**: ✅ COHERENT

| # | component | status | provider/model | latency | tokens | cost | detail |
|---|---|---|---|--:|--:|--:|---|
| 1 | driver (local model) | ✅ PASS | local / /model | 951ms | 42 | $0.0000 | ```python reversed_string = original_string[::-1] ``` |
| 2 | classifier (difficulty) | ✅ PASS | local-logic | 175ms | — | — | difficulty=easy reasons=['no complexity signals'] |
| 3 | watchdog | ✅ PASS | local-logic | 277ms | — | — | armed=True no_progress=True spiral=False |
| 4 | conductor.steer | ✅ PASS | deepinfra / deepseek-ai/DeepSeek-V4-Flash | 2990ms | 62 | $0.0000 | An agent should ask for a steer when the task is ambiguous, risks high-stakes errors, or r |
| 5 | research (SearXNG) | ✅ PASS | searxng-local | 3363ms | — | — | src=https://www.w3schools.com/python/python_lists_comprehension.asp chars=198 |
| 6 | research.corpus | ✅ PASS | disk | 274ms | — | — | path=dryrun/web/python---list-comprehension---w3schools.com.md chars=198 |
| 7 | knowledge-graph | ✅ PASS | kg:embedded | 74ms | — | — | backend=embedded entities=22 recall_outgoing=['cites'] |
| 8 | codebase-rag | ✅ PASS | bm25+graph | 168ms | — | — | indexed=True hits=3 |
| 9 | conductor.synth + brief_assemble | ✅ PASS | deepinfra / deepseek-ai/DeepSeek-V4-Pro | 3279ms | — | $0.0001 | deepinfra:deepseek-ai/DeepSeek-V4-Pro -> ```python def total(xs): return sum(xs) ``` |
| 10 | verify (deterministic gate) | ✅ PASS | ruff+mypy+pytest | 2773ms | — | — | green_passed=True red_caught=True |
| 11 | conductor.draft pool | ✅ PASS | cerebras,groq | 1736ms | — | — | candidates=3 skipped=[] |
| 12 | search.verifier-select | ✅ PASS | verify-gate | 3094ms | — | — | selected=cand_a verdicts={'cand_a': True, 'cand_b': True, 'cand_bad': False} (buggy reject |
| 13 | research.banyan (UCB1) | ✅ PASS | local-bandit | 289ms | — | — | sel1=dryrun-research(explore) -> update -> sel2=dryrun-build; saturated=False (thin data) |
| 14 | checkpoint | ✅ PASS | git | 193ms | — | — | sha=0be9e0f23164… reverted=True restored='v = 1' |
| 15 | escalation ladder (DRY) | ✅ PASS | (no spend) | 148ms | — | $0.0000 | route_escalated=False route=local; escalate_rung=OFF->proceed_local=True |

## Per-step detail (input → output)

### 1. driver (local model) — ✅ PASS
- action: trivial coding subtask
- provider/model: local / /model
- in:  `Reverse a string in Python in one line. Reply with ONLY the code.`
- out: ````python reversed_string = original_string[::-1] ````
- latency: 951ms · cost: $0.0000

### 2. classifier (difficulty) — ✅ PASS
- action: classify_difficulty
- provider/model: local-logic
- in:  `{'file_count': 1, 'novelty': 'low', 'test_failures': 0}`
- out: `difficulty=easy reasons=['no complexity signals']`
- latency: 175ms

### 3. watchdog — ✅ PASS
- action: arm budget + progress + spiral
- provider/model: local-logic
- in:  `task=dryrun-task wall=120s turns=10`
- out: `armed=True no_progress=True spiral=False`
- latency: 277ms

### 4. conductor.steer — ✅ PASS
- action: run_role(steer)
- provider/model: deepinfra / deepseek-ai/DeepSeek-V4-Flash
- in:  `steer nudge`
- out: `An agent should ask for a steer when the task is ambiguous, risks high-stakes errors, or requires human judgment, and should proceed when the task is clearly defined, low-risk, or within its proven capabilities.`
- latency: 2990ms · cost: $0.0000

### 5. research (SearXNG) — ✅ PASS
- action: one source, tiny query
- provider/model: searxng-local
- in:  `python list comprehension`
- out: `src=https://www.w3schools.com/python/python_lists_comprehension.asp chars=198`
- latency: 3363ms

### 6. research.corpus — ✅ PASS
- action: write on-disk corpus .md
- provider/model: disk
- in:  `namespace=dryrun chars=198`
- out: `path=dryrun/web/python---list-comprehension---w3schools.com.md chars=198`
- latency: 274ms

### 7. knowledge-graph — ✅ PASS
- action: record entity+edge, recall
- provider/model: kg:embedded
- in:  `decision -[cites]-> corpus doc`
- out: `backend=embedded entities=22 recall_outgoing=['cites']`
- latency: 74ms

### 8. codebase-rag — ✅ PASS
- action: index_document + search_code
- provider/model: bm25+graph
- in:  `index 1 doc; query 'how to build a list'`
- out: `indexed=True hits=3`
- latency: 168ms

### 9. conductor.synth + brief_assemble — ✅ PASS
- action: assemble brief + directive
- provider/model: deepinfra / deepseek-ai/DeepSeek-V4-Pro
- in:  `brief est_tokens=440 sources_live={'plan_md': False, 'kg': True, 'rag': True, 'checkpoints': False, 'watchdog': True}`
- out: `deepinfra:deepseek-ai/DeepSeek-V4-Pro -> ```python def total(xs): return sum(xs) ````
- latency: 3279ms · cost: $0.0001

### 10. verify (deterministic gate) — ✅ PASS
- action: green passes, red caught
- provider/model: ruff+mypy+pytest
- in:  `good_mod.py (+test) and a syntax-broken bad_mod.py`
- out: `green_passed=True red_caught=True`
- latency: 2773ms

### 11. conductor.draft pool — ✅ PASS
- action: parallel_draft fan-out
- provider/model: cerebras,groq
- in:  `best-of-N over present free pool`
- out: `candidates=3 skipped=[]`
- latency: 1736ms

### 12. search.verifier-select — ✅ PASS
- action: best-of-N, verifier selects green
- provider/model: verify-gate
- in:  `3 candidates (2 correct, 1 buggy)`
- out: `selected=cand_a verdicts={'cand_a': True, 'cand_b': True, 'cand_bad': False} (buggy rejected=True)`
- latency: 3094ms

### 13. research.banyan (UCB1) — ✅ PASS
- action: register x2, select, update, saturation
- provider/model: local-bandit
- in:  `2 namespaces, optimistic prior`
- out: `sel1=dryrun-research(explore) -> update -> sel2=dryrun-build; saturated=False (thin data)`
- latency: 289ms

### 14. checkpoint — ✅ PASS
- action: checkpoint + revert-to-green
- provider/model: git
- in:  `init repo, commit v1, break to v2, revert`
- out: `sha=0be9e0f23164… reverted=True restored='v = 1'`
- latency: 193ms

### 15. escalation ladder (DRY) — ✅ PASS
- action: route(hard) + escalate rung (mocked/off)
- provider/model: (no spend)
- in:  `hard task; cloud tiers off-by-default; no Opus key`
- out: `route_escalated=False route=local; escalate_rung=OFF->proceed_local=True`
- latency: 148ms · cost: $0.0000

## What this proves

Every component fired in one end-to-end pass (or cleanly skipped with a reason in this mode). The local driver is the one hard dependency; cloud steps are mode-gated and presence-gated, degrading to local without crashing the run — the anti-Frankenstein property. The verify gate caught a red change (cannot declare done on red), and the best-of-N verifier rejected a buggy candidate.
