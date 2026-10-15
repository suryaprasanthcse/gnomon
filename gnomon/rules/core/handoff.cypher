// BODY - Handoff@1 (R7): the holder passes a live lease to another skilled, active agent
MATCH (a:Agent {id: $agent})-[l:HOLDS]->(t:Task {id: $p.task})
WHERE l.closed_seq IS NULL AND l.until_pt > now AND t.state = 'claimed'
MATCH (b:Agent {id: $p.to})-[:HAS_SKILL]->(:Skill)<-[:NEEDS]-(t)
WHERE b.active = true AND b <> a AND $p.ttl_ms > 0 AND $p.ttl_ms <= $MAX_TTL_MS
WITH DISTINCT KCTX, a, b, t, l
SET l.closed_seq = nseq, l.close_reason = 'handoff'
CREATE (b)-[:HOLDS {from_seq: nseq, until_pt: now + $p.ttl_ms, from_agent: a.id, rw: $pid}]->(t)
WITH KCTX
