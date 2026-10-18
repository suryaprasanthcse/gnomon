# Demo video script · three minutes

Recorded by the author; the published video is linked from the README (Demo video section).
About 365 spoken words: roughly 2:30 of speech at 150 words a minute, leaving about 30 seconds for terminal output to land. Track names follow the Graph Hacks brief: 01 Connected Data, 02 Agent Memory, 03 Company Brain.

## Before you hit record

Do this off camera, in one PowerShell window at the repository root, with the font large:

```powershell
docker compose up -d --wait
function gk { uv run python -m gnomon.cli @args }
$env:GNOMON_FALKORDB_URL = "localhost:6379"
gk run --graph t:demo --seed 7 | Out-Null     # so the audit and why commands below have a graph; you re-run it live in row 2
```

- Use a second tab for `README.md` and `docs/design/master.md` section 26.
- Each proof run prints `PROOF <name>: PASS`. Proofs P1, P3, A, E, F take seconds; **C, D, P4 and G take longer**. Either run them once before recording and cut the wait in the edit, or speed the footage up. Do not talk over a wait.
- `gk` is `uv run python -m gnomon.cli`. The live audit is `gk audit` (K1 to K6), and `gk why` walks one entity back to its cause.

## Script

| Time | [Visual / Terminal Action] | [Spoken Voiceover] |
|---|---|---|
| 0:00 | Title card: **GNOMON**, "an operating system for agents, inside FalkorDB". Then the terminal: `docker compose up -d --wait` finishing healthy. | Agents forget, hallucinate and trip over each other. GNOMON is an operating system for agents, and every part of it runs inside FalkorDB. |
| 0:12 | `gk run --graph t:demo --seed 7` runs; the JSON ends with `"resolved": true` and the crash block showing `"replay": "DUPLICATE"`. | One command: genesis, a seeded company, an incident, four agents, and one agent killed mid-task. It resolves, and the replayed claim is rejected as a duplicate. |
| 0:25 | Switch to README, section **Architecture** (the diagram), then scroll to **Scoring and cost model**. Highlight `GRAPH.COPY`, `algo.pageRank`, "UDF". | Without FalkorDB there is no product. The kernel is a Cypher UDF library, so every change is one atomic query. Every what-if is a GRAPH.COPY branch. Advice comes from native algo procedures: PageRank, betweenness, connected components. |
| 0:40 | **Track 01.** Run `.\scripts\demo_proofs.ps1 A E`. Two lines: `PROOF A: PASS`, `PROOF E: PASS`. While it runs, show the README scoring block (`benefit`, `harm`, `score_num`). | Track one, connected data. The Executive copies the graph once per candidate action, walks the dependency edges to find the blast radius, and scores each branch with integer arithmetic. The live graph never moves. |
| 0:58 | Show the tool table in README (**Algorithms and the MCP surface**): `rank_critical`, `brokers`, `failure_domains`, `decide`, `why`. Point at "Evidence". | Agents reach all of this through twenty MCP tools. A committed decision keeps its Evidence paths, so you see not just what was chosen but the subgraph that justified it. |
| 1:12 | **Track 02.** Run `.\scripts\demo_proofs.ps1 P1 P3`. Show `PROOF P1: PASS`, `PROOF P3: PASS`. Show the README sentence "50 claims give 1 `APPLIED` and 49 `REJECTED_GUARD`". | Track two, agent memory. Tasks are atomic leases. Fifty agents claim one task at once: one wins, forty-nine are refused. Kill an agent mid-lease and the lease expires, another agent continues, and nothing runs twice. |
| 1:30 | README **Bitemporal time** table: `valid_at`, `known_at_seq`. Then run `.\scripts\demo_proofs.ps1 D`. `PROOF D: PASS`. | Every fact carries two intervals: valid-from to valid-to for the world, and transaction start to end for what we believed. Tenants are separate graphs that share nothing but ordered messages, and the federation audit stays green. |
| 1:50 | **Track 03.** Run `.\scripts\demo_proofs.ps1 P4 F`. Both PASS. Then `gk why --graph t:demo --uid svc:s15`. Show the `rule`, `agent` and `causes` fields. | Track three, the company brain. Ask who owned a service last Tuesday, as we knew it on Wednesday: that is an as-of query, and Q1 to Q3 answer it differently on purpose. Ask why something is true, and `why` walks back to the rewrite and agent that made it. |
| 2:12 | Run `.\scripts\demo_proofs.ps1 C`. `PROOF C: PASS`. Show README **Queries** row Q5 ("canonical slow scan"). | And the brain tunes itself. A slow ownership query is measured, trialled on a shadow copy, and fixed by a logged index change: the plan goes from a label scan to an index scan, and the catalogue audit stays green. |
| 2:27 | `gk audit --graph t:demo`: `"ok": true`. Then `.\scripts\demo_proofs.ps1 G`: `PROOF G: PASS`. | Every guarantee is checkable. The audit recomputes the hash chain and replays the whole log. Proof G injects three corruptions into live graphs, and each is caught by the checks we predicted. |
| 2:45 | README, section **AI assistance disclosure**, then **Known limits**. End on the repository URL. | Built tests first with an AI assistant, disclosed from the ledger, and the known limits are written down. GNOMON: agents that can be audited. |

## Sync notes

- Every command above is real: `scripts/demo_proofs.ps1 <names>` prints `PROOF <name>: PASS` or `FAIL`, and `gk run`, `gk audit`, `gk why` print one JSON document. Per-proof detail is the pytest output behind each PASS line; this script shows the README and the pass line rather than claiming numbers the terminal does not print.
- Mapping to the proofs (master section 26): Track 01 is A and E; Track 02 is P1, P3 and D; Track 03 is P4, F and C; G is the closing sweep. P2 and P5 are not in the three-minute cut; the table below lists every proof in case you extend it.
- The service id `svc:s15` is the bad-deploy service of seed 7. If you change the seed, read the id from the `bad_deploy` block of the `gk run` output.

## All twelve proofs (for a longer cut)

| Proof | Command | Claim |
|---|---|---|
| Proof P1 | `scripts\demo_proofs.ps1 P1` | Races are found before they run |
| Proof P2 | `scripts\demo_proofs.ps1 P2` | Invented ids and stale facts are refused |
| Proof P3 | `scripts\demo_proofs.ps1 P3` | Crash and exactly-once |
| Proof P4 | `scripts\demo_proofs.ps1 P4` | Why, and time travel |
| Proof P5 | `scripts\demo_proofs.ps1 P5` | Determinism and tamper evidence |
| Proof A | `scripts\demo_proofs.ps1 A` | Counterfactual action |
| Proof B | `scripts\demo_proofs.ps1 B` | Gate, veto, re-selection |
| Proof C | `scripts\demo_proofs.ps1 C` | Self-optimizing migration |
| Proof D | `scripts\demo_proofs.ps1 D` | Federation ordering |
| Proof E | `scripts\demo_proofs.ps1 E` | Federated blast radius |
| Proof F | `scripts\demo_proofs.ps1 F` | Counterfactual time travel |
| Proof G | `scripts\demo_proofs.ps1 G` | Audit sweep |
