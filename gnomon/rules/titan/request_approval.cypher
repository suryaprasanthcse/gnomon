// BODY - RequestApproval@1 (R16): a pending approval for one decision attempt, bound to its sha.
OPTIONAL MATCH (ex:Approval {aid: $p.aid})
WITH KCTX, count(ex) AS n
WHERE n = 0 AND $p.ttl_ms > 0 AND $p.ttl_ms <= $APPROVAL_TTL_MS
MATCH (i:Incident {id: $p.incident})
WHERE i.state = 'open'
WITH KCTX, i
CREATE (ap:Approval {aid: $p.aid, did: $p.did, decision_sha: $p.decision_sha, rule: $p.rule,
        params_sha: $p.params_sha, summary: $p.summary, state: 'pending',
        requested_seq: nseq, expires_pt: now + $p.ttl_ms, rw: $pid})
CREATE (ap)-[:FOR]->(i)
WITH KCTX
