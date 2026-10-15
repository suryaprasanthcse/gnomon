// BODY - ProposeCause@1 (R10): a root-cause fact from an open incident to a deploy or service
MATCH (i:Incident {id: $p.incident}), (x:Entity {uid: $p.cause})
WHERE i.state = 'open' AND (x:Deploy OR x:Service) AND $p.confidence >= 0 AND $p.confidence <= 100
OPTIONAL MATCH (i)-[rc:ROOT_CAUSE]->(x)
WHERE rc.te = $INF AND rc.vt = $INF
WITH KCTX, i, x, count(rc) AS n
WHERE n = 0
UNWIND (CASE WHEN size($p.cites) = 0 THEN [null] ELSE $p.cites END) AS ct
OPTIONAL MATCH (cs:Entity {uid: ct.s})-[cf]-()
WHERE cf.fid = ct.fid AND LIVE(cf)
WITH KCTX, i, x, count(DISTINCT cf.fid) AS live_cites
WHERE live_cites = size($p.cites)
CREATE (i)-[:ROOT_CAUSE {fid: $pid + '#0', vf: now, vt: $INF, tf: now, tt: $INF, ts: nseq, te: $INF, by: $agent,
        confidence: $p.confidence, rw: $pid}]->(x)
WITH KCTX
