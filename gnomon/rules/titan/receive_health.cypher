// BODY - ReceiveMessage.health@1 (X3, prelude P2): the provider's health lands on the matching ExternalService.
// An unknown service is consumed as 'unroutable' and an older message as 'superseded': a rejection would block the
// FIFO channel for good. Two ExternalServices for one remote service are a configuration error, never a silent pick.
// An isolated provider reads as down.
MATCH (ch:Channel {key: 'in:' + $m.origin_tenant})
OPTIONAL MATCH (xs:ExternalService {remote_id: $m.svc})
WHERE xs.tenant = $m.origin_tenant
WITH KCTX, ch, head(collect(xs)) AS x, count(xs) AS nx
WHERE nx <= 1
WITH KCTX, ch, x, (x IS NOT NULL AND coalesce(x.health_s_pt, -1) <= $m.s_pt) AS fresh
CREATE (:XMsg {id: $m.id, chan: ch.key, chan_seq: $m.chan_seq, kind: 'health', svc: $m.svc,
        health: $m.health, mode: $m.mode, digest: $m.digest, s_pt: $m.s_pt, s_lc: $m.s_lc,
        origin_tenant: $m.origin_tenant, origin_seq: $m.origin_seq, origin_rw: $m.origin_rw,
        r_pt: now, r_lc: now_lc, r_seq: nseq,
        state: CASE WHEN x IS NULL THEN 'unroutable' WHEN fresh THEN 'received' ELSE 'superseded' END,
        rw: $pid})
SET ch.next_seq = $m.chan_seq + 1
FOREACH (y IN CASE WHEN fresh THEN [x] ELSE [] END |
  SET y.health = CASE WHEN $m.mode = 'isolated' THEN 'down' ELSE $m.health END,
      y.remote_mode = $m.mode, y.health_s_pt = $m.s_pt, y.health_seq = nseq, y.rw = $pid)
WITH KCTX
