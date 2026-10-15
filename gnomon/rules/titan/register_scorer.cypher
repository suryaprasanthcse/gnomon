// BODY - RegisterScorer@1 (M5): a new scorer version replaces the active one of the same name.
// The active ones are picked out of the collected rows with a list comprehension, not a WHERE on an OPTIONAL MATCH (FACTS S25).
OPTIONAL MATCH (same:Scorer {sha: $p.sha})
WITH KCTX, count(same) AS n_same
WHERE n_same = 0 AND $p.kind IN ['udf', 'measured']
OPTIONAL MATCH (prev:Scorer {name: $p.name})
WITH KCTX, collect(prev) AS all_prev
FOREACH (x IN [y IN all_prev WHERE y.active = true] | SET x.active = false, x.deactivated_seq = nseq)
CREATE (:Scorer {name: $p.name, sha: $p.sha, kind: $p.kind, version: $p.version,
        active: true, registered_seq: nseq, rw: $pid})
WITH KCTX
