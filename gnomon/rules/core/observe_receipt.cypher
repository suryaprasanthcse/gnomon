// BODY - Observe.receipt@1 (R1): a delivery receipt for a pending outbox row (section 11.1)
OPTIONAL MATCH (e:Event {source_id: $p.source_id})
WITH KCTX, e
WHERE e IS NULL AND $p.outcome IN ['delivered', 'failed']
MATCH (o:Outbox {pid: $p.outbox})
WHERE o.state = 'pending'
WITH KCTX, o, o.attempts + 1 AS na
WITH KCTX, o, na,
     CASE WHEN $p.outcome = 'delivered' THEN 'delivered'
          WHEN na >= $MAX_ATTEMPTS THEN 'dead'
          ELSE 'pending' END AS ns
CREATE (:Event:Entity {uid: 'evt:' + $p.source_id, id: $p.source_id, source_id: $p.source_id, kind: 'receipt',
        observed_pt: $p.observed_pt, rw: $pid})
SET o.attempts = na, o.state = ns, o.last_seq = nseq
WITH KCTX
