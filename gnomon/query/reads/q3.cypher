// Q3 - What the system believed after rewrite $S about a service's history
MATCH (h:Head)
MATCH (s:Service {id: $svc})-[f:OWNED_BY]->(t:Team)
WHERE f.ts <= $S AND $S < f.te
RETURN h.seq AS read_seq, t.id AS team, f.vf AS valid_from, f.vt AS valid_to, f.fid AS fid
ORDER BY f.vf
