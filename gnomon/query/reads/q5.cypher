// Q5 - Ownership change feed in a valid-time window (unanchored edge scan; the optimizer indexes OWNED_BY.vf)
// `NOT f.te < $INF` is `f.te = $INF` (te never exceeds INF) written so the planner cannot pick the genesis te index over vf (D-60)
MATCH (h:Head)
MATCH (s:Service)-[f:OWNED_BY]->(t:Team)
WHERE NOT f.te < $INF AND f.vf >= $T1 AND f.vf < $T2
RETURN h.seq AS read_seq, s.id AS service, t.id AS team, f.vf AS changed_at, f.fid AS fid
ORDER BY changed_at
