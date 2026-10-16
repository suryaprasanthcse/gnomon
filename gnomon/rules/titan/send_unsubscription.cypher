// BODY - SendUnsubscription@1 (X6, in the consumer's graph): once nothing depends on the provider's service any more and
// a subscription is still outstanding (the last subscribe on this channel is newer than the last unsubscribe), say so.
MATCH (c:Service {id: $p.consumer_svc})
MATCH (x:Entity {uid: 'ext:' + $p.ext})
WHERE x:ExternalService AND x.tenant = $p.peer
OPTIONAL MATCH (c)-[cur:DEPENDS_ON]->(x)
WITH KCTX, c, x, sum(CASE WHEN cur.te = $INF AND cur.vt = $INF THEN 1 ELSE 0 END) AS n_live
WHERE n_live = 0
OPTIONAL MATCH (sx:XMsg)
WHERE sx.state IN ['queued', 'delivered'] AND sx.chan = 'out:' + $p.peer AND sx.consumer_svc = c.id AND sx.provider_svc = x.remote_id
WITH KCTX, c, x,
     max(CASE WHEN sx.kind = 'subscribe' THEN sx.chan_seq END) AS last_sub,
     max(CASE WHEN sx.kind = 'unsubscribe' THEN sx.chan_seq END) AS last_unsub
WHERE coalesce(last_sub, -1) > coalesce(last_unsub, -1)
MATCH (ch:Channel {key: 'out:' + $p.peer})
WITH KCTX, c, x, ch, ch.next_seq AS cseq, h.tenant + '/' + $pid AS mid
CREATE (:XMsg {id: mid, chan: ch.key, chan_seq: cseq, kind: 'unsubscribe',
        consumer_svc: c.id, provider_svc: x.remote_id,
        digest: Kernel.digest(['unsubscribe', h.tenant, mid, cseq, now, now_lc, c.id, x.remote_id]),
        s_pt: now, s_lc: now_lc, origin_tenant: h.tenant, origin_seq: nseq, origin_rw: $pid,
        state: 'queued', rw: $pid})
SET ch.next_seq = cseq + 1
WITH KCTX
