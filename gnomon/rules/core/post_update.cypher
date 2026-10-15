// BODY - PostUpdate@1 (R9): a status update on an open incident, every cite live now
MATCH (i:Incident {id: $p.incident})
WHERE i.state = 'open' AND size($p.cites) >= 1
UNWIND $p.cites AS ct
OPTIONAL MATCH (cs:Entity {uid: ct.s})-[cf]-()
WHERE cf.fid = ct.fid AND LIVE(cf)
WITH KCTX, i, count(DISTINCT cf.fid) AS live_cites
WHERE live_cites = size($p.cites)
CREATE (m:Message:Entity {uid: 'msg:' + $p.message_id, id: $p.message_id, incident: i.id, body: $p.body,
        author: $agent, rw: $pid})
CREATE (m)-[:ABOUT]->(i)
FOREACH (ct IN $p.cites |
  MERGE (cn:Entity {uid: ct.s})
  CREATE (m)-[:CITES {fid: ct.fid}]->(cn))
CREATE (:Outbox {pid: $pid, kind: 'post_update', target: 'status-page', incident: i.id, payload: $p.body,
        state: 'pending', attempts: 0})
WITH KCTX
