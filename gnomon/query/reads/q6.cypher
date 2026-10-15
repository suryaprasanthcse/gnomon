// Q6 - Who owned each service an incident affected, at the moment it opened
// The opening rewrite is matched with WHERE, not an inline map that references i (D-30).
MATCH (h:Head)
MATCH (i:Incident {id: $incident})-[:AFFECTS]->(s:Service)
MATCH (r:Rewrite)
WHERE r.seq = i.opened_seq
MATCH (s)-[f:OWNED_BY]->(t:Team)
WHERE f.vf <= r.pt AND r.pt < f.vt AND f.ts < r.seq AND r.seq <= f.te
RETURN h.seq AS read_seq, s.id AS service, t.id AS team, f.fid AS fid
