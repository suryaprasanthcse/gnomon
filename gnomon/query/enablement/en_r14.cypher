// EN-R14 - RollbackDeploy: the current root-cause deploy of an unhealthy service and each earlier verified deploy
MATCH (h:Head)
MATCH (i:Incident {id: $incident})-[rc:ROOT_CAUSE]->(bad:Deploy)
WHERE i.state = 'open' AND rc.te = $INF AND rc.vf <= h.pt AND h.pt < rc.vt
MATCH (s:Service)-[cur:DEPLOYED]->(bad)
WHERE s.id = bad.service AND cur.te = $INF AND cur.vt = $INF AND s.health IN ['degraded', 'down']
MATCH (s)-[:DEPLOYED]->(prev:Deploy)
WHERE prev.verified = true AND prev.id <> bad.id
RETURN DISTINCT h.seq AS read_seq, i.id AS incident, s.id AS svc, bad.id AS bad, prev.id AS prev
ORDER BY prev DESC
