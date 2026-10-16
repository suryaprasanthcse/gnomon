// BODY - ReceiveMessage.subscribe@1 (X3, prelude P2): a consumer tenant subscribes to one of this tenant's services.
// 'unroutable' when the service is unknown, 'noop' when the subscription is already current, else the Consumer
// leaf and its CONSUMES fact (with its CONSUMES_NOW projection) are created. Current versions are counted with a
// sum(CASE) so no filter sits on the OPTIONAL MATCH (DECISIONS D-25).
MATCH (ch:Channel {key: 'in:' + $m.origin_tenant})
OPTIONAL MATCH (s:Service {id: $m.provider_svc})
OPTIONAL MATCH (cn:Entity {uid: 'cons:' + $m.origin_tenant + '/' + $m.consumer_svc})
OPTIONAL MATCH (cn)-[cur:CONSUMES]->(s)
WITH KCTX, ch, s, cn, sum(CASE WHEN cur.te = $INF AND cur.vt = $INF THEN 1 ELSE 0 END) AS live
WITH KCTX, ch, s, cn,
     CASE WHEN s IS NULL THEN 'unroutable' WHEN live > 0 THEN 'noop' ELSE 'received' END AS st
CREATE (:XMsg {id: $m.id, chan: ch.key, chan_seq: $m.chan_seq, kind: 'subscribe',
        consumer_svc: $m.consumer_svc, provider_svc: $m.provider_svc, hard: $m.hard,
        criticality: $m.criticality, digest: $m.digest, s_pt: $m.s_pt, s_lc: $m.s_lc,
        origin_tenant: $m.origin_tenant, origin_seq: $m.origin_seq, origin_rw: $m.origin_rw,
        r_pt: now, r_lc: now_lc, r_seq: nseq, state: st, rw: $pid})
SET ch.next_seq = $m.chan_seq + 1
FOREACH (_ IN CASE WHEN s IS NOT NULL AND cn IS NULL THEN [1] ELSE [] END |
  CREATE (:Consumer:Entity {uid: 'cons:' + $m.origin_tenant + '/' + $m.consumer_svc,
          id: $m.origin_tenant + '/' + $m.consumer_svc, tenant: $m.origin_tenant,
          remote_svc: $m.consumer_svc, criticality: $m.criticality, rw: $pid}))
FOREACH (_ IN CASE WHEN st = 'received' THEN [1] ELSE [] END |
  MERGE (c2:Entity {uid: 'cons:' + $m.origin_tenant + '/' + $m.consumer_svc})
  CREATE (c2)-[:CONSUMES {fid: $pid + '#0', hard: $m.hard, vf: now, vt: $INF,
                          tf: now, tt: $INF, ts: nseq, te: $INF, rw: $pid}]->(s)
  CREATE (c2)-[:CONSUMES_NOW {fid: $pid + '#0', hard: $m.hard}]->(s))
WITH KCTX
