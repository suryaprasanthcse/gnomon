// -- E0 - evidence (kernel-owned; after Q0 when rule.requires_decision) ---------------------------
// No WHERE in this segment: the rewrite is already written, so a filter here could only drop the rows that carry
// the evidence while the rewrite stays applied. Everything it creates was proved by P1 or comes from the envelope.
CREATE (dec:Decision {id: $d.did, attempt: $d.attempt, sha: $d.sha, kind: $d.kind,
        proposer: $d.proposer, rationale_ref: $d.rationale_ref, scorer: $d.scorer,
        scorer_sha: $d.scorer_sha, read_seq: $read_seq, hops: $d.hops,
        lambda_n: $d.lambda_n, lambda_d: $d.lambda_d, tau: $d.tau, margin: $d.margin,
        weight_scale: $d.weight_scale, footprint_digest: $d.footprint_digest,
        workload_digest: $d.workload_digest, rw: $pid})
CREATE (dec)-[:COMMITTED_AS]->(r)
FOREACH (cd IN $d.candidates |
  CREATE (dec)-[:CONSIDERED]->(:Candidate {cid: $d.did + ':' + toString(cd.k), k: cd.k,
          rule: cd.rule, params_sha: cd.params_sha, outcome: cd.outcome, reason: cd.reason,
          benefit: cd.benefit, harm: cd.harm, teams_new: cd.teams_new,
          score_num: cd.score_num, plan_before: cd.plan_before, plan_after: cd.plan_after,
          measure_digest: cd.measure_digest, won: cd.won}))
FOREACH (e IN $d.evidence |
  CREATE (dec)-[:JUSTIFIED_BY]->(ev:Evidence {eid: $d.did + ':' + toString(e.ord), ord: e.ord,
          algo: e.algo, role: e.role, path: e.path, rw: $pid})
  FOREACH (ct IN e.cites |
    MERGE (cn:Entity {uid: ct.s})
    CREATE (ev)-[:CITES {fid: ct.fid}]->(cn)))
WITH h, r, dec
OPTIONAL MATCH (ap:Approval {aid: $d.aid})
FOREACH (x IN CASE WHEN ap IS NULL THEN [] ELSE [ap] END |
  CREATE (x)-[:APPROVES]->(dec))
RETURN r.seq AS seq, r.hash AS hash, dec.id AS decision
