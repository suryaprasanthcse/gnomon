// EN-R18 - RestoreService: diverted services of the region that have a verified current deploy
MATCH (h:Head)
MATCH (i:Incident {id: $incident})-[:AFFECTS]->(a:Service)
WHERE i.state = 'open'
MATCH (a)-[:DEP_NOW*0..4]-(s:Service)-[cur:DEPLOYED]->(d:Deploy)
WHERE s.mode IN ['isolated', 'failed_over'] AND cur.te = $INF AND cur.vt = $INF AND d.verified = true
RETURN DISTINCT h.seq AS read_seq, i.id AS incident, s.id AS svc
ORDER BY svc
