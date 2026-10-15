// ── BODY · RegisterAgent@1 (M6) ────────────────────────────────────────────
// register: a new id, a role from $ROLES other than kernel, every skill already registered.
// deactivate: an active, non-kernel agent.
OPTIONAL MATCH (ex:Agent {id: $p.id})
OPTIONAL MATCH (sk:Skill)
WHERE sk.id IN $p.skills
WITH KCTX, ex, collect(DISTINCT sk) AS sks
WHERE ($p.op = 'register' AND ex IS NULL AND $p.role IN $ROLES AND $p.role <> 'kernel'
       AND size(sks) = size($p.skills))
   OR ($p.op = 'deactivate' AND ex IS NOT NULL AND ex.active = true AND ex.role <> 'kernel')
FOREACH (_ IN CASE WHEN $p.op = 'register' THEN [1] ELSE [] END |
  CREATE (a:Agent:Entity {uid: 'agent:' + $p.id, id: $p.id, role: $p.role, active: true, rw: $pid})
  FOREACH (x IN sks | CREATE (a)-[:HAS_SKILL]->(x)))
FOREACH (x IN CASE WHEN $p.op = 'deactivate' THEN [ex] ELSE [] END |
  SET x.active = false, x.deactivated_seq = nseq)
WITH KCTX
