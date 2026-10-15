// ── BODY · Genesis@1 ───────────────────────────────────────────────────────
// Seeds the kernel graph from params PG has already validated: rules, fact types, skills, agents
// (with HAS_SKILL to skills PG proved exist) and the genesis rows of the physical catalogue.
FOREACH (r IN $p.rules |
  CREATE (:Rule {name: r.name, version: r.version, sha: r.sha, body: r.body, prelude: r.prelude,
                 evidence: r.evidence, class: r.class, layer: r.layer, family: r.family, caller: r.caller,
                 reads: r.reads, writes: r.writes, key_templates: r.key_templates,
                 view_templates: r.view_templates, guard_probes_json: r.guard_probes_json,
                 requires_decision: r.requires_decision, gated: r.gated, lint_sha: r.lint_sha,
                 active: true, registered_seq: nseq, rw: $pid}))
FOREACH (ft IN $p.fact_types |
  CREATE (:FactType {rel: ft.rel, s_labels: ft.s_labels, o_labels: ft.o_labels, cardinality: ft.cardinality,
                     projection: ft.projection, attrs: ft.attrs}))
FOREACH (sk IN $p.skills |
  CREATE (:Skill:Entity {uid: 'skill:' + sk, id: sk, rw: $pid}))
FOREACH (ag IN $p.agents |
  CREATE (a:Agent:Entity {uid: 'agent:' + ag.id, id: ag.id, role: ag.role, active: true, rw: $pid})
  FOREACH (sid IN ag.skills |
    MERGE (s:Skill {id: sid})
    CREATE (a)-[:HAS_SKILL]->(s)))
FOREACH (ix IN $p.base_indexes |
  CREATE (:IndexEntry {key: ix.key, entity: ix.entity, label: ix.label, prop: ix.prop, state: 'operational',
                       origin: 'genesis', op: 'create', requested_seq: nseq, settled_seq: nseq, settled_pt: now,
                       rw: $pid}))
FOREACH (cx IN $p.base_constraints |
  CREATE (:ConstraintEntry {key: cx.key, kind: cx.kind, entity: cx.entity, label: cx.label, props: cx.props,
                            state: 'operational'}))
WITH KCTX
