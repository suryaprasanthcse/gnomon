// ── Q0 · postlude (every rule) ─────────────────────────────────────────────
// Advance Head, version every write key, append the chained Rewrite, link it to its causes.
// `prev: h.hash` is read by CREATE before `SET h.hash`; the hash is computed in its own SET from
// stored properties, so it never depends on evaluation order inside one SET.
SET h.seq = nseq, h.pt = now, h.lc = now_lc
FOREACH (k IN $write_keys |
  MERGE (kv:KeyVersion {key: k})
  SET kv.seq = nseq, kv.rw = $pid)
CREATE (r:Rewrite {id: $pid, seq: nseq, pt: now, lc: now_lc, rule: $rule, rule_sha: $rule_sha, agent: $agent,
                   params: $params_json, attach: $attach, read_seq: $read_seq, view_keys: $view_keys,
                   write_keys: $write_keys, prev: h.hash})
SET r.hash = Kernel.chain(r.prev, r.id, r.seq, r.pt, r.lc, r.rule_sha, r.agent, r.params, r.read_seq,
                          Kernel.digest($view_keys))
SET h.hash = r.hash
FOREACH (cid IN causes |
  MERGE (x:Rewrite {id: cid})
  CREATE (r)-[:CAUSED_BY]->(x))
WITH h, r
