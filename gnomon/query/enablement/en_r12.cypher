// EN-R12 - IsolateService: unhealthy services of the incident's region      view: Incident:{incident}:state, Service:{svc}:state
MATCH (h:Head)
MATCH (i:Incident {id: $incident})-[:AFFECTS]->(a:Service)
WHERE i.state = 'open'
MATCH (a)-[:DEP_NOW*0..4]-(s:Service)
WHERE s.mode = 'normal' AND s.health IN ['degraded', 'down']
RETURN DISTINCT h.seq AS read_seq, i.id AS incident, s.id AS svc
ORDER BY svc
