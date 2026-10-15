// EN-R10 - ProposeCause: deploys of the affected service in the window that are not already a current cause
//          view: Incident:{incident}:state, ROOT_CAUSE:inc:{incident}:*
// The current-cause exclusion (D-31) keeps the query sound against guard G6.
MATCH (h:Head)
MATCH (i:Incident {id: $incident})-[:AFFECTS]->(s:Service)
WHERE i.state = 'open'
MATCH (s)-[f:DEPLOYED]->(d:Deploy)
WHERE f.te = $INF AND f.vt >= h.pt - $CAUSE_WINDOW_MS
OPTIONAL MATCH (i)-[rc:ROOT_CAUSE]->(d)
WITH h, i, s, d, f, sum(CASE WHEN rc.te = $INF AND rc.vt = $INF THEN 1 ELSE 0 END) AS n_rc
WHERE n_rc = 0
RETURN h.seq AS read_seq, i.id AS incident, d.uid AS cause, d.version AS version,
       s.uid AS service, f.vf AS deployed_at, f.fid AS fid
ORDER BY deployed_at DESC
