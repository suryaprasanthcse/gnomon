// BODY - CompleteTask@1 (R6): the holder of a live lease completes the task
MATCH (a:Agent {id: $agent})-[l:HOLDS]->(t:Task {id: $p.task})
WHERE l.closed_seq IS NULL AND l.until_pt > now AND t.state = 'claimed'
WITH KCTX, t, l
SET t.state = 'done', t.result = $p.result, t.done_seq = nseq
SET l.closed_seq = nseq, l.close_reason = 'completed'
WITH KCTX
