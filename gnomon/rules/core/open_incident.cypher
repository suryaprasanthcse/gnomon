// BODY - OpenIncident@1 (R4). The duplicate-id check is two indexed lookups (section 9.5 note).
MATCH (al:Alert {id: $p.alert})-[:ON]->(s:Service)
WHERE s.id = $p.service AND al.signature = $p.signature AND size($p.tasks) >= 1
OPTIONAL MATCH (al)-[tr:TRIGGERED]->()
OPTIONAL MATCH (di:Entity {uid: 'inc:' + $p.incident})
OPTIONAL MATCH (dt:Entity)
WHERE dt.uid IN [t IN $p.tasks | 'task:' + t.id]
WITH KCTX, al, s, count(tr) AS n_tr, count(di) AS n_inc, count(dt) AS n_task
WHERE n_tr = 0 AND n_inc = 0 AND n_task = 0
OPTIONAL MATCH (open:Incident {service: s.id})
WHERE open.signature = al.signature AND open.state = 'open'
WITH KCTX, al, s, count(open) AS n_open
WHERE n_open = 0
OPTIONAL MATCH (sk:Skill)
WHERE sk.id IN [t IN $p.tasks | t.skill]
WITH KCTX, al, s, collect(DISTINCT sk.id) AS skills
WHERE all(t IN $p.tasks WHERE t.skill IN skills)
CREATE (i:Incident:Entity {uid: 'inc:' + $p.incident, id: $p.incident, service: s.id, signature: al.signature,
        state: 'open', escalated: false, holds: 0, opened_seq: nseq, rw: $pid})
CREATE (al)-[:TRIGGERED]->(i)
CREATE (i)-[:AFFECTS]->(s)
FOREACH (t IN $p.tasks |
  CREATE (tk:Task:Entity {uid: 'task:' + t.id, id: t.id, incident: $p.incident, kind: t.kind, state: 'open',
          rw: $pid})
  CREATE (tk)-[:PART_OF]->(i)
  MERGE (need:Skill {id: t.skill})
  CREATE (tk)-[:NEEDS]->(need))
WITH KCTX
