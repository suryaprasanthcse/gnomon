// EN-R9 - PostUpdate: citeable live facts about an open incident's service
//         view: Incident:{incident}:state, {rel}:{s}:* for each returned fact
MATCH (h:Head)
MATCH (i:Incident {id: $incident})-[:AFFECTS]->(sv:Service)
WHERE i.state = 'open'
MATCH (sv)-[f]-(:Entity)
WHERE f.te = $INF AND f.vf <= h.pt AND h.pt < f.vt
RETURN h.seq AS read_seq, i.id AS incident, type(f) AS rel,
       startNode(f).uid AS s, endNode(f).uid AS o, f.fid AS fid
UNION
MATCH (h:Head)
MATCH (i:Incident {id: $incident})-[f:ROOT_CAUSE]->(x:Entity)
WHERE i.state = 'open' AND f.te = $INF AND f.vt = $INF
RETURN h.seq AS read_seq, i.id AS incident, 'ROOT_CAUSE' AS rel, i.uid AS s, x.uid AS o, f.fid AS fid
