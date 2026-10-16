// BODY - IndexSettled@1 (M2): the reconciler's attested outcome of a requested or retiring index (master 16.12).
// The guard does not read db.indexes(): physical state is audited by K8, so replay stays deterministic.
MATCH (ix:IndexEntry {key: $p.key})
WHERE (ix.state = 'requested' AND $p.outcome IN ['operational', 'failed'])
   OR (ix.state = 'retiring'  AND $p.outcome IN ['retired', 'failed'])
WITH KCTX, ix,
     CASE WHEN ix.state = 'retiring' AND $p.outcome = 'failed' THEN 'operational'
          ELSE $p.outcome END AS ns
SET ix.state = ns, ix.settled_seq = nseq, ix.settled_pt = now,
    ix.ddl_ms = $p.ddl_ms, ix.detail = $p.detail
WITH KCTX
