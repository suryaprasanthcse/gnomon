// Q5 - Ownership change feed in a valid-time window (unanchored edge scan; the optimizer indexes OWNED_BY.vf)
MATCH (h:Head)
MATCH (s:Service)-[f:OWNED_BY]->(t:Team)
WHERE f.te = $INF AND f.vf >= $T1 AND f.vf < $T2
RETURN h.seq AS read_seq, s.id AS service, t.id AS team, f.vf AS changed_at, f.fid AS fid
ORDER BY changed_at
