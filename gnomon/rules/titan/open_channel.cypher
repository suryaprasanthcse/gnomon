// BODY - OpenChannel@1 (X0): one end of a peering, opened once. `next_seq` is the next chan_seq to send (out) or to accept (in).
OPTIONAL MATCH (ex:Channel {key: $p.dir + ':' + $p.peer})
WITH KCTX, count(ex) AS n
WHERE n = 0 AND $p.dir IN ['in', 'out'] AND $p.peer <> h.tenant
CREATE (:Channel {key: $p.dir + ':' + $p.peer, peer: $p.peer, dir: $p.dir, next_seq: 1,
        sys_peering: $p.sys_peering, rw: $pid})
WITH KCTX
