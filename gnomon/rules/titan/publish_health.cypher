// BODY - PublishHealth@1 (X2): one health message per consumer tenant, for a service whose state changed since it was
// last published. $p.peers must be exactly the tenants that consume the service now (the relay cites that read, so a
// subscription that lands first makes the proposal stale). Each channel advances once; the UNWIND ends in an aggregate.
MATCH (s:Service {id: $p.svc})
OPTIONAL MATCH (s)<-[:CONSUMES_NOW]-(cn:Consumer)
WITH KCTX, s, collect(DISTINCT cn.tenant) AS peers
WHERE size(peers) >= 1
  AND (size(peers) = size($p.peers) AND all(x IN $p.peers WHERE x IN peers))
  AND coalesce(s.published_state, '') <> s.health + '/' + s.mode
MATCH (ch:Channel)
WHERE ch.key IN [x IN $p.peers | 'out:' + x]
WITH KCTX, s, collect(ch) AS chans
WHERE size(chans) = size($p.peers)
WITH KCTX, s, chans, s.health + '/' + s.mode AS st
UNWIND chans AS ch
WITH KCTX, s, st, ch, ch.next_seq AS cseq, h.tenant + '/' + $pid + '/' + ch.peer AS mid
CREATE (:XMsg {id: mid, chan: ch.key, chan_seq: cseq, kind: 'health', svc: s.id, health: s.health, mode: s.mode,
        digest: Kernel.digest(['health', h.tenant, mid, cseq, now, now_lc, s.id, s.health, s.mode]),
        s_pt: now, s_lc: now_lc, origin_tenant: h.tenant, origin_seq: nseq, origin_rw: $pid,
        state: 'queued', rw: $pid})
SET ch.next_seq = cseq + 1
WITH KCTX, s, st, count(ch) AS sent
SET s.published_state = st
WITH KCTX
