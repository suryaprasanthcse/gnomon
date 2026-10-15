// BODY - Observe.alert@1 (R1): a new alert event on a known service
OPTIONAL MATCH (e:Event {source_id: $p.source_id})
WITH KCTX, e
WHERE e IS NULL
MATCH (s:Service {id: $p.service})
WITH KCTX, s
CREATE (ev:Event:Entity {uid: 'evt:' + $p.source_id, id: $p.source_id, source_id: $p.source_id, kind: 'alert',
        observed_pt: $p.observed_pt, payload_sha: $p.payload_sha, rw: $pid})
CREATE (al:Alert:Entity {uid: 'alr:' + $p.source_id, id: $p.source_id, service: s.id, signature: $p.signature,
        severity: $p.severity, rw: $pid})
CREATE (ev)-[:RAISED]->(al)
CREATE (al)-[:ON]->(s)
WITH KCTX
