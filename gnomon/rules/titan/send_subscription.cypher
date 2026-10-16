// BODY - SendSubscription@1 (X5, in the consumer's graph): tell the provider tenant that a service depends on one of its
// services. The dependency's projection edge records the subscription's seq, so a subscription is sent once.
MATCH (c:Service {id: $p.consumer_svc})-[d:DEP_NOW]->(x:ExternalService {id: $p.ext})
WHERE d.subscribed_seq IS NULL AND x.tenant = $p.peer
MATCH (ch:Channel {key: 'out:' + $p.peer})
WITH KCTX, c, d, x, ch, ch.next_seq AS cseq, h.tenant + '/' + $pid AS mid
CREATE (:XMsg {id: mid, chan: ch.key, chan_seq: cseq, kind: 'subscribe',
        consumer_svc: c.id, provider_svc: x.remote_id, hard: d.hard, criticality: c.criticality,
        digest: Kernel.digest(['subscribe', h.tenant, mid, cseq, now, now_lc,
                               c.id, x.remote_id, toString(d.hard), c.criticality]),
        s_pt: now, s_lc: now_lc, origin_tenant: h.tenant, origin_seq: nseq, origin_rw: $pid,
        state: 'queued', rw: $pid})
SET ch.next_seq = cseq + 1
SET d.subscribed_seq = nseq
WITH KCTX
