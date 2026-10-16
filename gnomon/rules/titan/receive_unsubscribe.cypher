// BODY - ReceiveMessage.unsubscribe@1 (X3, prelude P2): the consumer's current CONSUMES version is closed (te, tt), its
// valid time ends now in a new version, and the CONSUMES_NOW projection goes. 'noop' when there is no current version.
// The projection is matched by a WHERE, not an inline map on an earlier variable (DECISIONS D-30).
MATCH (ch:Channel {key: 'in:' + $m.origin_tenant})
OPTIONAL MATCH (cn:Entity {uid: 'cons:' + $m.origin_tenant + '/' + $m.consumer_svc})-[cur:CONSUMES]->(s:Service {id: $m.provider_svc})
WHERE cur.te = $INF AND cur.vt = $INF
OPTIONAL MATCH (cn)-[pn:CONSUMES_NOW]->(s)
WHERE pn.fid = cur.fid
WITH KCTX, ch, cn, s, cur, pn, CASE WHEN cur IS NULL THEN 'noop' ELSE 'received' END AS st
CREATE (:XMsg {id: $m.id, chan: ch.key, chan_seq: $m.chan_seq, kind: 'unsubscribe',
        consumer_svc: $m.consumer_svc, provider_svc: $m.provider_svc, digest: $m.digest,
        s_pt: $m.s_pt, s_lc: $m.s_lc, origin_tenant: $m.origin_tenant, origin_seq: $m.origin_seq,
        origin_rw: $m.origin_rw, r_pt: now, r_lc: now_lc, r_seq: nseq, state: st, rw: $pid})
SET ch.next_seq = $m.chan_seq + 1
FOREACH (x IN CASE WHEN cur IS NULL THEN [] ELSE [cur] END |
  SET x.te = nseq, x.tt = now
  CREATE (cn)-[:CONSUMES {fid: $pid + '#0', prev_fid: x.fid, hard: x.hard, vf: x.vf, vt: now,
                          tf: now, tt: $INF, ts: nseq, te: $INF, rw: $pid}]->(s))
FOREACH (x IN CASE WHEN pn IS NULL THEN [] ELSE [pn] END | DELETE x)
WITH KCTX
