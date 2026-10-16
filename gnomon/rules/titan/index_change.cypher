// BODY - IndexChange@1 (M1): the logged intent to create or drop one index. The reconciler runs the DDL (master 16.10).
// Optimizer-owned entries hang off the Head node by OPT_INDEX, so the in-flight and budget counts are anchored on a bound
// variable (lint L3); they are plain counts over an unfiltered OPTIONAL MATCH (FACTS S25).
MATCH (hd:Head)
WHERE hd.tenant = h.tenant
OPTIONAL MATCH (hd)-[:OPT_INDEX]->(oi:IndexEntry)
WITH KCTX, hd,
     sum(CASE WHEN oi.state IN ['requested', 'retiring'] THEN 1 ELSE 0 END) AS n_busy,
     sum(CASE WHEN oi.state = 'operational' THEN 1 ELSE 0 END) AS n_opt
WHERE n_busy = 0
  AND $p.op IN ['create', 'drop'] AND $p.entity IN ['node', 'edge']
  AND $p.key = $p.entity + ':' + $p.label + ':' + $p.prop
OPTIONAL MATCH (ix:IndexEntry {key: $p.key})
WITH KCTX, hd, ix, n_opt
WHERE ($p.op = 'create' AND n_opt < $OPT_INDEX_BUDGET
       AND (ix IS NULL OR (ix.state IN ['retired', 'failed']
                           AND ix.settled_pt + $OPT_COOLDOWN_MS <= now)))
   OR ($p.op = 'drop' AND ix IS NOT NULL AND ix.state = 'operational'
       AND ix.origin = 'optimizer' AND ix.settled_pt + $OPT_COOLDOWN_MS <= now)
FOREACH (_ IN CASE WHEN ix IS NULL THEN [1] ELSE [] END |
  CREATE (hd)-[:OPT_INDEX]->(:IndexEntry {key: $p.key, entity: $p.entity, label: $p.label, prop: $p.prop,
          state: 'requested', origin: 'optimizer', op: 'create', requested_seq: nseq, rw: $pid}))
FOREACH (x IN CASE WHEN ix IS NULL THEN [] ELSE [ix] END |
  SET x.state = CASE WHEN $p.op = 'create' THEN 'requested' ELSE 'retiring' END,
      x.op = $p.op, x.requested_seq = nseq, x.rw = $pid)
WITH KCTX
