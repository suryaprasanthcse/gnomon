// BODY - RestoreService@1 (R18): return an isolated or failed-over service to normal, health from its verified deploy.
MATCH (s:Service {id: $p.svc})-[cur:DEPLOYED]->(d:Deploy)
WHERE s.mode IN ['isolated', 'failed_over'] AND cur.te = $INF AND cur.vt = $INF AND d.verified = true
MATCH (i:Incident {id: $p.incident})
WHERE i.state = 'open'
WITH DISTINCT KCTX, s, d
SET s.mode = 'normal', s.active_replica = null, s.health = d.expected_health,
    s.health_basis = 'restore', s.mode_seq = nseq
CREATE (:Outbox {pid: $pid, kind: 'restore', target: s.id, state: 'pending', attempts: 0})
WITH KCTX
