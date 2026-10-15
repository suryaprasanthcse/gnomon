// EN-R13 - FailoverService: (service, replica) pairs. Unhealthy replicas are returned on purpose, so the Executive
// can show a guard-pruned future.
MATCH (h:Head)
MATCH (i:Incident {id: $incident})-[:AFFECTS]->(a:Service)
WHERE i.state = 'open'
MATCH (a)-[:DEP_NOW*0..4]-(s:Service)
WHERE s.mode = 'normal' AND s.health IN ['degraded', 'down']
MATCH (r:Service)-[f:REPLICA_OF]->(s)
WHERE f.te = $INF AND f.vf <= h.pt AND h.pt < f.vt
RETURN DISTINCT h.seq AS read_seq, i.id AS incident, s.id AS svc, r.id AS replica
ORDER BY svc, replica
