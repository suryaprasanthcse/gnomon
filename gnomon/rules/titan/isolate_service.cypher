// BODY - IsolateService@1 (R12): cut an unhealthy service in the incident's region off from its dependents.
MATCH (i:Incident {id: $p.incident})-[:AFFECTS]->(a:Service)
WHERE i.state = 'open'
MATCH (a)-[:DEP_NOW*0..4]-(s:Service {id: $p.svc})
WHERE s.mode = 'normal' AND s.health IN ['degraded', 'down']
WITH DISTINCT KCTX, s
SET s.mode = 'isolated', s.health = 'down', s.health_basis = 'isolated', s.mode_seq = nseq
CREATE (:Outbox {pid: $pid, kind: 'isolate', target: s.id, state: 'pending', attempts: 0})
WITH KCTX
