// BODY - FailoverService@1 (R13): move an unhealthy service in the region onto a healthy live replica.
MATCH (i:Incident {id: $p.incident})-[:AFFECTS]->(a:Service)
WHERE i.state = 'open'
MATCH (a)-[:DEP_NOW*0..4]-(s:Service {id: $p.svc})
WHERE s.mode = 'normal' AND s.health IN ['degraded', 'down']
MATCH (r:Service {id: $p.replica})-[f:REPLICA_OF]->(s)
WHERE LIVE(f) AND r.health = 'ok' AND r.mode = 'normal'
WITH DISTINCT KCTX, s, r, r.health AS replica_health  // carried in a variable: L7 forbids reading Service.health inside the SET
SET s.mode = 'failed_over', s.active_replica = r.id, s.health = replica_health,
    s.health_basis = 'failover', s.mode_seq = nseq
CREATE (:Outbox {pid: $pid, kind: 'failover', target: s.id, to: r.id, state: 'pending', attempts: 0})
WITH KCTX
