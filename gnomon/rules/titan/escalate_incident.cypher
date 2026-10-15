// BODY - EscalateIncident@1 (R15): page the owning team. The owner is the lowest team id (min is order-independent, L2).
MATCH (i:Incident {id: $p.incident})-[:AFFECTS]->(s:Service)
WHERE i.state = 'open'
OPTIONAL MATCH (s)-[:OWNER_NOW]->(tm:Team)
WITH KCTX, i, min(tm.id) AS team
WITH KCTX, i, team, coalesce(i.holds, 0) + 1 AS nh
SET i.escalated = true, i.holds = nh, i.escalated_seq = nseq
CREATE (:Outbox {pid: $pid, kind: 'page', target: coalesce(team, 'on-call'), incident: i.id,
        state: 'pending', attempts: 0})
WITH KCTX
