// BODY - SendNotice@1 (X1): queue a notice on an out-channel. The message gets the channel's next chan_seq and a digest
// over its fields; the channel advances in the same rewrite. The sequence number is read before the SET (L7).
MATCH (ch:Channel {key: 'out:' + $p.peer})
WHERE size($p.body) <= $NOTICE_MAX
WITH KCTX, ch, ch.next_seq AS cseq, h.tenant + '/' + $pid AS mid
CREATE (:XMsg {id: mid, chan: ch.key, chan_seq: cseq, kind: 'notice', incident_ref: $p.incident, body: $p.body,
        digest: Kernel.digest(['notice', h.tenant, mid, cseq, now, now_lc, $p.incident, $p.body]),
        s_pt: now, s_lc: now_lc, origin_tenant: h.tenant, origin_seq: nseq, origin_rw: $pid,
        state: 'queued', rw: $pid})
SET ch.next_seq = cseq + 1
WITH KCTX
