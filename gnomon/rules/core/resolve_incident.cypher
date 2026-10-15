// BODY - ResolveIncident@1 (R11): every task done, a current root cause, the service ok and normal
MATCH (i:Incident {id: $p.incident})-[:AFFECTS]->(s:Service)
WHERE i.state = 'open' AND s.health = 'ok' AND s.mode = 'normal'
OPTIONAL MATCH (t:Task)-[:PART_OF]->(i)
WITH KCTX, i, count(t) AS n_all, sum(CASE WHEN t.state = 'done' THEN 1 ELSE 0 END) AS n_done
WHERE n_all = n_done
// The current-version filter sits in the aggregate, not in the OPTIONAL MATCH: after the aggregation above,
// the planner pushes an OPTIONAL MATCH filter below its traverse and drops every row (FACTS S25).
OPTIONAL MATCH (i)-[rc:ROOT_CAUSE]->()
WITH KCTX, i, sum(CASE WHEN rc.te = $INF AND rc.vt = $INF THEN 1 ELSE 0 END) AS n_rc
WHERE n_rc >= 1
SET i.state = 'resolved', i.resolved_seq = nseq
WITH KCTX
