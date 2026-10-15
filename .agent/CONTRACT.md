GNOMON AGENT OPERATING CONTRACT  (applies to every module)

C1  SOURCE OF TRUTH. docs/design/master.md is the specification. Where code and spec disagree,
    the spec wins, unless .agent/DECISIONS.md records an approved deviation (id, reason, approver).
C2  READ BUDGET. Read only .agent/CONTRACT.md, .agent/STATE.md and the spec sections your module
    lists. To follow a cross-reference (§n), read that section only. Never load the whole spec.
C3  TESTS FIRST. For each behaviour batch: (a) write the tests; (b) run them and confirm they FAIL
    for the stated reason (missing module, NotImplemented, assertion), not for a harness error;
    (c) save the failing output to .agent/evidence/<module>/red-<n>.txt; (d) commit
    "test(<module>): <batch>"; (e) implement; (f) run until green; save green-<n>.txt;
    (g) commit "feat(<module>): <batch>".
    DRIVER MODE (ORRERY-Lite, patch P2): steps (b)–(d), (f)–(g), all commits, evidence files,
    STATE.md, LEDGER.md and gates/*.json are performed by the driver. Agents NEVER run git commit,
    never write .agent/{CONTRACT,STATE,LEDGER}.md, .agent/evidence/** or gates/**. The test-writing
    role writes tests only; the implementing role never edits test files. During implementation run
    only the current module's marker (inner loop); the driver runs the cumulative gate once (P3).
C4  NEVER WEAKEN TESTS. Do not delete, skip, xfail, loosen or rename a test to make a gate pass.
    If a test is wrong, stop, write to .agent/QUESTIONS.md, and wait for a human decision.
C5  REAL DATABASE. Integration, property, stateful, concurrency, mutation, e2e and chaos tests
    run against the Testcontainers falkordb/falkordb image pinned in STATE.md. FalkorDB is never mocked.
C6  SCOPE. Create or modify only the files listed in your module's SCOPE, plus tests and .agent/*.
    If you need a change elsewhere, stop and write to QUESTIONS.md.
C7  NO PLACEHOLDERS. No TODO, pass-only bodies, NotImplementedError, or skipped branches in any
    file covered by your gate when you finish.
C8  DETERMINISM. Seeds fixed. Wall-clock time only through the injected clock. Ordered iteration
    wherever output is hashed.
C9  RULE 8. Author all code in this repository. Never paste code blocks from docs/design/ into
    source files; implement against the tests.
C10 PLATFORM FACTS. Behaviour the spec marks as a spike assumption is used only as recorded under
    "platform facts" in STATE.md. If a needed fact is missing, stop and ask.
C11 HANDOFF. Before ending: update STATE.md, write gates/<GATE>.json via scripts/gate.py,
    append one line to LEDGER.md, write docs/explain/<module>.md, commit "gate(<GATE>): green".
    DRIVER MODE (P2): the driver performs the whole handoff. The gate is first recorded as
    "provisional" (scripts/gate.py <GATE> --provisional, patch P1) and becomes "green" only after the
    human answers the explain-back through the driver (`orrery explain <module>`).
C12 STOP CONDITIONS. Stop and write to QUESTIONS.md on: spec ambiguity that changes behaviour;
    a failing precondition; a spike fact contradicting the spec; any need to edit an existing
    test; a context budget near exhaustion (record exactly where you stopped in STATE.md).
