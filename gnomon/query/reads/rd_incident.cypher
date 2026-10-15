// incident_status - One incident with its tasks and their leases      view: Incident:{id}:state, Task:*
// Closed leases come back too (closed_seq set); the service keeps the live one. No filter on an OPTIONAL MATCH (S25).
MATCH (h:Head)
MATCH (i:Incident {id: $incident})-[:AFFECTS]->(s:Service)
OPTIONAL MATCH (t:Task)-[:PART_OF]->(i)
OPTIONAL MATCH (a:Agent)-[l:HOLDS]->(t)
RETURN h.seq AS read_seq, i.id AS incident, i.state AS state, s.id AS service, t.id AS task, t.state AS task_state,
       a.id AS holder, l.closed_seq AS closed_seq, l.until_pt AS lease_until
ORDER BY task
