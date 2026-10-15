// EN-R11 - ResolveIncident: incidents meeting every guard      view: Incident:{incident}:state, phantom Task:*
// The root-cause count is a sum(CASE) over an unfiltered OPTIONAL MATCH (FACTS S25, D-25).
MATCH (h:Head)
MATCH (i:Incident)-[:AFFECTS]->(s:Service)
WHERE i.state = 'open' AND s.health = 'ok' AND s.mode = 'normal'
OPTIONAL MATCH (t:Task)-[:PART_OF]->(i)
WITH h, i, count(t) AS n_all, sum(CASE WHEN t.state = 'done' THEN 1 ELSE 0 END) AS n_done
WHERE n_all = n_done
OPTIONAL MATCH (i)-[rc:ROOT_CAUSE]->()
WITH h, i, sum(CASE WHEN rc.te = $INF AND rc.vt = $INF THEN 1 ELSE 0 END) AS n_rc
WHERE n_rc >= 1
RETURN h.seq AS read_seq, i.id AS incident
ORDER BY incident
