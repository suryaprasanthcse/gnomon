// EN-R15 - EscalateIncident: always available while the incident is open
MATCH (h:Head)
MATCH (i:Incident {id: $incident})-[:AFFECTS]->(s:Service)
WHERE i.state = 'open'
RETURN h.seq AS read_seq, i.id AS incident, s.id AS service
