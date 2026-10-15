// BODY - ExpireLease@1 (R8): close an expired lease against the rewrite's own (recorded) now
MATCH (:Agent)-[l:HOLDS]->(t:Task {id: $p.task})
WHERE l.closed_seq IS NULL AND l.until_pt <= now AND t.state = 'claimed'
WITH KCTX, t, l
SET l.closed_seq = nseq, l.close_reason = 'expired'
SET t.state = 'open'
WITH KCTX
