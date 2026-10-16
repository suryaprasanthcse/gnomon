// BODY - AckMessage@1 (X4, prelude P3, in the sender's graph): the receiver's receipt marks the queued message delivered.
MATCH (x:XMsg {id: $m.id})
WHERE x.state = 'queued' AND x.chan = 'out:' + $m.r_tenant AND x.digest = $m.digest
WITH KCTX, x
SET x.state = 'delivered', x.r_pt = $m.r_pt, x.r_lc = $m.r_lc, x.r_seq = $m.r_seq,
    x.r_rw = $m.r_rw, x.acked_seq = nseq
WITH KCTX
