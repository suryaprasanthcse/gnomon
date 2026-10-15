// ── BODY · RegisterRule@1 (M3) ─────────────────────────────────────────────
// The sha is recomputed in-query from def_json; the version must be the next for the name; the
// previous active version is deactivated in the same rewrite. Genesis is never re-registered.
OPTIONAL MATCH (same:Rule {sha: $p.sha})
WITH KCTX, count(same) AS n_same
WHERE n_same = 0 AND $p.def.name <> 'Genesis'
  AND CASE WHEN $p.def_json IS NULL THEN false ELSE Kernel.sha256($p.def_json) = $p.sha END
OPTIONAL MATCH (anyv:Rule {name: $p.def.name})
WITH KCTX, coalesce(max(anyv.version), 0) AS top, collect(CASE WHEN anyv.active = true THEN anyv END) AS prevs
WHERE $p.def.version = top + 1
FOREACH (x IN prevs | SET x.active = false, x.deactivated_seq = nseq)
CREATE (:Rule {name: $p.def.name, version: $p.def.version, sha: $p.sha, body: $p.def.body,
               prelude: $p.def.prelude, evidence: $p.def.evidence, class: $p.def.class, layer: $p.def.layer,
               family: $p.def.family, caller: $p.def.caller, reads: $p.def.reads, writes: $p.def.writes,
               key_templates: $p.def.key_templates, view_templates: $p.def.view_templates,
               guard_probes_json: $p.def.guard_probes_json, requires_decision: $p.def.requires_decision,
               gated: $p.def.gated, lint_sha: $p.def.lint_sha, active: true, registered_seq: nseq, rw: $pid})
WITH KCTX
