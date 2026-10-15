// Q4 - Services a team owned at valid time $T (current belief)
MATCH (h:Head)
MATCH (s:Service)-[f:OWNED_BY]->(t:Team {id: $team})
WHERE f.te = $INF AND f.vf <= $T AND $T < f.vt
RETURN h.seq AS read_seq, s.id AS service, f.fid AS fid
ORDER BY service
