// EN-R5 - ClaimTask: open tasks the caller is skilled for      view: Task:{task}:state, HOLDS:*:{task}
MATCH (h:Head)
MATCH (a:Agent {id: $agent})-[:HAS_SKILL]->(:Skill)<-[:NEEDS]-(t:Task)
WHERE t.state = 'open'
RETURN DISTINCT h.seq AS read_seq, t.id AS task, t.kind AS kind, t.incident AS incident
ORDER BY task
