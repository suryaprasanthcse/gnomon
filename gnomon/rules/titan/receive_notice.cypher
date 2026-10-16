// BODY - ReceiveMessage.notice@1 (X3, prelude P2): the notice becomes an XMsg in this graph's own log, and operators are told.
MATCH (ch:Channel {key: 'in:' + $m.origin_tenant})
WITH KCTX, ch
CREATE (:XMsg {id: $m.id, chan: ch.key, chan_seq: $m.chan_seq, kind: 'notice',
        incident_ref: $m.incident_ref, body: $m.body, digest: $m.digest,
        s_pt: $m.s_pt, s_lc: $m.s_lc, origin_tenant: $m.origin_tenant, origin_seq: $m.origin_seq,
        origin_rw: $m.origin_rw, r_pt: now, r_lc: now_lc, r_seq: nseq, state: 'received', rw: $pid})
SET ch.next_seq = $m.chan_seq + 1
CREATE (:Outbox {pid: $pid, kind: 'notice_in', target: 'operators', payload: $m.body, state: 'pending', attempts: 0})
WITH KCTX
