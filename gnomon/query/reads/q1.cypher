// Q1 - Owner of a service at valid time $T, as known after rewrite $S
MATCH (h:Head)
MATCH (s:Service {id: $svc})-[f:OWNED_BY]->(t:Team)
WHERE f.vf <= $T AND $T < f.vt AND f.ts <= $S AND $S < f.te
RETURN h.seq AS read_seq, t.id AS team, f.fid AS fid
