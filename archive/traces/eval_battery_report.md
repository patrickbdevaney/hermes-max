# hermes-max eval battery — per-capability report

Each capability was driven through a REAL `hermes -z … --yolo` agent turn (or, for the
passive watchdog, a direct probe), and the assertion checks the REAL-WORLD EFFECT
(file / KG db / corpus / git commit / MEMORY.md / live-log span), not just a 200.

| Capability | Result | Tool evidence | Real-effect verified | Break-point (if failed) |
|---|---|---|---|---|
| `codebase-rag` | ✅ PASS | rag span=1 | agent indexed+retrieved the planted symbol 'zzq_marker_0716' | - |

## Agent tasks used
- **codebase-rag**: Index the CURRENT directory as a code repository, then use code search to tell me the names of the functions defined in this repo.

**Totals: 1 pass · 0 fail · 0 skip** of 1 capabilities.

_Isolation: RAG/KG/corpus snapshotted and restored; MEMORY.md backed up and restored;
filesystem tests ran in a temp dir. Real state was not polluted._
