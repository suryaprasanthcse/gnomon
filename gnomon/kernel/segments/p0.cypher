// ── P0 · standard prelude (every rule except R0) ───────────────────────────
// Pin the rule, admit the caller, refuse a known pid or a view from the future, refuse a stale view,
// then tick the clock once. Nothing below this segment may filter on anything the prelude has not.
MATCH (h:Head)
MATCH (rule:Rule {name: $rule, sha: $rule_sha})
WHERE rule.active = true
MATCH (ag:Agent {id: $agent})
WHERE ag.active = true AND ag.role IN rule.caller
OPTIONAL MATCH (dup:Rewrite {id: $pid})
WITH h, rule, dup
WHERE dup IS NULL AND $read_seq <= h.seq
OPTIONAL MATCH (kv:KeyVersion)
WHERE kv.key IN $view_keys
WITH h, rule, sum(CASE WHEN kv.seq > $read_seq THEN 1 ELSE 0 END) AS stale, collect(DISTINCT kv.rw) AS causes
WHERE stale = 0
WITH h, causes, <TICK> AS c
WITH h, causes, h.seq + 1 AS nseq, c.pt AS now, c.lc AS now_lc
