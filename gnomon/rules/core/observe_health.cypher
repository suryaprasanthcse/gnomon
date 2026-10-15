// BODY - Observe.health@1 (R1): a newer health observation of a service
OPTIONAL MATCH (e:Event {source_id: $p.source_id})
WITH KCTX, e
WHERE e IS NULL
MATCH (s:Service {id: $p.service})
WHERE $p.health IN ['ok', 'degraded', 'down']
  AND coalesce(s.health_observed_pt, -1) < $p.observed_pt
WITH KCTX, s
CREATE (:Event:Entity {uid: 'evt:' + $p.source_id, id: $p.source_id, source_id: $p.source_id, kind: 'health',
        observed_pt: $p.observed_pt, rw: $pid})
SET s.health = $p.health, s.health_observed_pt = $p.observed_pt, s.health_basis = 'observed'
WITH KCTX
