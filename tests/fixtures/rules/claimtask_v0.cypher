// ── ClaimTask_v0 · seeded demo bug (lint fixture ONLY; never registered) ────
// No lease guard and no task-state guard: two agents can both claim the same task.
MATCH (t:Task {id: $p.task}), (a:Agent {id: $agent})
WITH KCTX, t, a
CREATE (a)-[:HOLDS {from_seq: nseq, until_pt: now + $p.ttl, from_agent: $agent, rw: $pid}]->(t)
SET t.state = 'claimed'
WITH KCTX
