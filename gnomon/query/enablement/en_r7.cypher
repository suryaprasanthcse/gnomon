// EN-R7 - Handoff: (task, target) pairs      view: HOLDS:*:{task}, Agent:{to}:state
MATCH (h:Head)
MATCH (a:Agent {id: $agent})-[l:HOLDS]->(t:Task)
WHERE l.closed_seq IS NULL AND l.until_pt > h.pt AND t.state = 'claimed'
MATCH (b:Agent)-[:HAS_SKILL]->(:Skill)<-[:NEEDS]-(t)
WHERE b.active = true AND b.id <> a.id
RETURN DISTINCT h.seq AS read_seq, t.id AS task, b.id AS to
ORDER BY task, to
