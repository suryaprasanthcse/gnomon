# STATE
current_module:        A06          status: provisional (awaiting explain-back)
last_green_gate:       G2a
falkordb_image:        falkordb/falkordb@sha256:107455e862a824e45e8e9f5a7fbfdf5e00a08b14e13eae2577c72fd9e3f59ba6
python:                3.12.x
platform_facts:        (full text in .agent/FACTS.md; each line is enforced by its tests/spikes test)
                       S1: PASS → UDFs in WHERE/SET of writes; maps and null-holding lists are not storable as properties
                       S2: PASS → FOREACH over CASE lists, nested MERGE, DELETE inside FOREACH
                       S3: PASS → writes to one graph are serialized (CAS: 1 winner of 50)
                       S4: PASS → any error (UDF throw, unique or mandatory violation) rolls the whole query back
                       S5: PASS → copy of a 16k-rewrite log 140–162 ms (budget 1,000 ms)
                       S6: PASS → poll db.indexes() status "…: UNDER CONSTRUCTION" → "OPERATIONAL"
                       S7: PASS → EXPLAIN stable; fixtures in tests/fixtures/plans/
                       S8: FAIL → Cloud UDF support unverified; fallback: Docker Compose for live (D-3)
                       S10: PASS → copy cost linear or better (31/79/178 ms for 2k/8k/32k)
                       S11: PASS → UDF edges carry full source/target node objects
                       S12: PASS → MCP SDK 2.3.0 in-process round trip (MCPServer, snake_case fields)
                       S13: PASS → memory reclaimed after GRAPH.DELETE
                       S14: FAIL → MEMORY USAGE ignores graph size; fallback: index-count budget only (D-4)
                       S15: PASS → label predicates, *0..4, null omission, toString/startNode/endNode/properties, UNION
                       S16: PASS → "Query internal execution time" in every reply
                       S17: FAIL → algo.bfs is outgoing-only; fallback: var-length pattern upstream (D-5)
                       S18: PASS → db.indexes() YIELD label, properties, types, options, language, stopwords, entitytype, status, info
                       S19: PASS → Q5 uses Edge By Index Scan once OWNED_BY.vf is indexed
                       S20: PASS → IN $list served by Node By Index Scan
                       S21: PASS → writes ignore per-query TIMEOUT; keep TIMEOUT_DEFAULT = 0
                       S22: PASS → copies are complete during UDF reloads; never REPLACE a library under traffic
                       F1: PASS → GRAPH.COPY snapshot carries indexes and constraints; never overwrites a key
                       F2: PASS → FOREACH accepts CREATE/MERGE/SET/REMOVE/DELETE/nested, sees outer variables
                       F3: FAIL → DROP INDEX ON :R(p) fails; use DROP INDEX FOR ()-[f:R]-() ON (f.p) (D-6)
                       F4: PASS → GRAPH.CONSTRAINT replies PENDING, reaches OPERATIONAL; needs its index first
                       F5: PASS → PROFILE lines match master §16.3
                       F6: PASS → algo signatures as §10.5; maxFlow YIELD nodes, edges, edgeFlows, maxFlow
                       F7: PASS → UDF graph API read-only; traverse ignores `distance`
                       S25 (A06 hazard): FAIL → planner pushes an OPTIONAL MATCH filter below its traverse after an aggregation; filter in sum(CASE…) instead (D-25)
                       S24 (A05 hazard): FAIL → slow write with a multi-byte char at byte 2048 of its text aborts the server; send ASCII query text (D-20)
                       S23 (A02 hazard): FAIL → a property projected after an aggregate in RETURN/WITH panics the server; keep aggregates last (D-8)
fallbacks_in_effect:   [S8: Docker Compose for live (D-3), S14: index-count budget (D-4), S17: var-length upstream (D-5), F3: FOR-pattern relationship index drop (D-6), S23: aggregates last in RETURN/WITH (D-8), S24: comment-stripped ASCII wire text (D-20), S25: R11 counts root causes in sum(CASE) (D-25)]
kernel_sha:            267426dbc01ac862ac9667c84d2f018139bcfb8e784d5a00fb9124008bdc5f21 (Head.kernel_sha: udf/kernel.js + gnomon/kernel/composer.py + segments pg/p0/q0/ret, LF)
kernel_udf_sha:        768790ef5e3caa601e38847e027f8313a26417e14efefe24a552c840561e32bf (Kernel, udf/kernel.js, LF; = sha256 of GRAPH.UDF LIST Kernel WITHCODE)
layers_active:         []
open_questions:        0 (see QUESTIONS.md)
resume_point:          A06 checkpoint: human answers Q1-Q5 in docs/explain/A06.md, sets HUMAN-SIGNED: yes, then `uv run python scripts/gate.py G2b` and commit gate(G2b): green; next module A07 (gate G3)
module_table:          | module | gate | status | commit |
                       | A00    | H0   | green  | 384ce89 |
                       | A01    | G0   | green  | 15bd2c1 |
                       | A02    | G1a  | green  | 1cd38d9 |
                       | A03    | G1b  | green  | 12b4bed |
                       | A04    | G1c  | green  | 3aa5942 |
                       | A05    | G2a  | green  | 43deb49, 59c4153 |
                       | A06    | G2b  | provisional | 50ef436 |
