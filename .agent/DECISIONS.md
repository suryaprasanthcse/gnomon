# DECISIONS

The human owns these decisions (rule 13). The agent proposes; the human approves.

Format: `D-<n> | <date> | module | decision | reason | spec ref | approved-by: <human> | status`

D-1 | 2026-10-15 | A00 | Create `.gitignore` (venv, caches, coverage) and commit `uv.lock`, both outside the A00 SCOPE list | Without them `.venv/` and caches pollute every commit, and the pinned lockfile is what makes `uv sync --locked` in CI reproducible | RUNBOOK_A §2, §4.1, contract C6 | approved-by: — | needs-approval
D-2 | 2026-10-15 | A00 | Record gate H0 as `provisional` and commit the handoff as `gate(H0): provisional`, not `gate(H0): green` | Rule 12: a gate turns green only after the human answers the explain-back and sets HUMAN-SIGNED: yes. After signing, `python scripts/gate.py H0` re-runs it and the commit `gate(H0): green` follows | RUNBOOK_A §1 (rule 12), §4.5 step 6, patch P1, contract C11 | approved-by: — | needs-approval
