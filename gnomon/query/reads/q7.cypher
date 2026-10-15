// Q7 - Dependency topology at valid time $T (current belief): the input to T-F projections
MATCH (h:Head)
MATCH (a:Service)-[f:DEPENDS_ON]->(b)
WHERE f.te = $INF AND f.vf <= $T AND $T < f.vt
RETURN h.seq AS read_seq, a.uid AS from, b.uid AS to, f.hard AS hard, f.fid AS fid
