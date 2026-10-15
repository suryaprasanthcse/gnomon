// BODY - ClaimTask@1 (R5): a skilled agent claims an open task with no live lease
MATCH (a:Agent {id: $p.agent})-[:HAS_SKILL]->(:Skill)<-[:NEEDS]-(t:Task {id: $p.task})
WHERE $p.agent = $agent AND t.state = 'open' AND $p.ttl_ms > 0 AND $p.ttl_ms <= $MAX_TTL_MS
WITH DISTINCT KCTX, a, t
OPTIONAL MATCH (t)<-[l:HOLDS]-(:Agent)
WHERE l.closed_seq IS NULL AND l.until_pt > now
WITH KCTX, a, t, count(l) AS live
WHERE live = 0
CREATE (a)-[:HOLDS {from_seq: nseq, until_pt: now + $p.ttl_ms, rw: $pid}]->(t)
SET t.state = 'claimed'
WITH KCTX
