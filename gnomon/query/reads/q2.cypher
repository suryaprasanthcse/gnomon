// Q2 - Full current ownership history of a service
MATCH (h:Head)
MATCH (s:Service {id: $svc})-[f:OWNED_BY]->(t:Team)
WHERE f.te = $INF
RETURN h.seq AS read_seq, t.id AS team, f.vf AS valid_from, f.vt AS valid_to, f.fid AS fid
ORDER BY f.vf
