# Demo video script

Target length: seven minutes. Record it yourself, in your own voice, then replace `VIDEO-LINK-PENDING` in `README.md` with the link.
Every proof below is one command: `scripts/demo_proofs.sh <name>` (Windows: `scripts\demo_proofs.ps1 <name>`) with the compose stack up.
Show the command, show the PASS line, then say the claim in your own words. The claims are the rows of master section 26.

| Time | On screen | Say |
|---|---|---|
| 0:00 | Title card, repository tree | What GNOMON is: an event-sourced operating system for agents, every subsystem inside FalkorDB. Name the tracks: 01, 02, 03 |
| 0:30 | `docker compose up -d --wait`, then `uv run python -m gnomon.cli run --graph t:demo --seed 7` | Clean install to a resolved incident in two commands |
| 1:00 | Proof P1 | Races are found before they run: `ClaimTask_v0` is refused by lint L1; the fixed rule gives 1 `APPLIED` and 49 `REJECTED_GUARD` |
| 1:30 | Proof P2 | Models cannot corrupt memory or act on facts that changed: invented id, stale proposal naming the key |
| 2:00 | Proof P3 | Agents are data: `kill -9` mid-lease, the lease expires, the work continues, nothing runs twice |
| 2:30 | Proof P4 | Every change explains itself: `why` across four agents; Q1 against Q2 against Q3 |
| 3:00 | Proof P5 | Determinism and tamper evidence: equal state hash after replay; K4 and K1 name the tampered property and sequence |
| 3:30 | Proof A | Three futures priced in their own copies; the live `Head.hash` does not move; commit with evidence |
| 4:00 | Proof B | The analyst vetoes, the next best action is re-selected, the `APPROVES` edge exists only on what was committed |
| 4:30 | Proof C | A slow read becomes a logged index: Label Scan to Edge By Index Scan, with measured microseconds; K8 green |
| 5:00 | Proof D | Two tenants share nothing but ordered messages; a skewed sender is quarantined; K9 green |
| 5:30 | Proof E | The platform picks failover because isolation would harm two tenants; a subscription mid-decision forces a re-simulation |
| 6:00 | Proof F | `counterfactual` at an earlier sequence number picks a different action because ownership differed then |
| 6:30 | Proof G | K1 to K9 green; three mutations injected, each caught by its predicted checks (MU1 to MU32 are the full catalogue) |
| 7:00 | Known limits, AI-assistance section | Say plainly what is recorded, not removed (limit 15) and which parts the AI wrote |
