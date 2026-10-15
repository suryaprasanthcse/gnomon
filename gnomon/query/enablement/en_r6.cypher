// EN-R6 - CompleteTask: tasks the caller holds a live lease on      view: Task:{task}:state, HOLDS:*:{task}
MATCH (h:Head)
MATCH (a:Agent {id: $agent})-[l:HOLDS]->(t:Task)
WHERE l.closed_seq IS NULL AND l.until_pt > h.pt AND t.state = 'claimed'
RETURN h.seq AS read_seq, t.id AS task, l.until_pt AS lease_until
ORDER BY task
