// BODY - RollbackDeploy@1 (R14, gated): put the previous verified deploy back in place of the bad root cause.
MATCH (i:Incident {id: $p.incident})-[rc:ROOT_CAUSE]->(bad:Deploy {id: $p.bad})
WHERE i.state = 'open' AND LIVE(rc)
MATCH (s:Service {id: $p.svc})-[cur:DEPLOYED]->(bad)
WHERE bad.service = s.id AND cur.te = $INF AND cur.vt = $INF AND s.health IN ['degraded', 'down']
MATCH (prev:Deploy {id: $p.prev})
WHERE prev.service = s.id AND prev.verified = true AND prev.id <> bad.id
WITH DISTINCT KCTX, s, cur, bad, prev
SET cur.te = nseq, cur.tt = now
CREATE (s)-[:DEPLOYED {fid: $pid + '#e', prev_fid: cur.fid, vf: cur.vf, vt: now,
                       tf: now, tt: $INF, ts: nseq, te: $INF, rw: $pid}]->(bad)
CREATE (s)-[:DEPLOYED {fid: $pid + '#0', vf: now, vt: $INF,
                       tf: now, tt: $INF, ts: nseq, te: $INF, rw: $pid}]->(prev)
SET s.health = prev.expected_health, s.health_basis = 'rollback', s.mode_seq = nseq
CREATE (:Outbox {pid: $pid, kind: 'rollback', target: s.id, to: prev.id, state: 'pending', attempts: 0})
WITH KCTX
