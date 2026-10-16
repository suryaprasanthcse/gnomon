"""Mutation suite MU1–MU18 (master §21.2, Core rows) and MU19–MU21 (T-A rows, `LAYER_MUTATIONS`).

Each mutation corrupts a scratch copy (`t:<tenant>:replay:…`) out of band, dropping a constraint first where
one would block it, and names the checks that must detect it. Targets are found in the graph itself, so a
mutation applies to any history that holds the element it corrupts; otherwise MutationNotApplicable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import redis

from gnomon.kernel.registry import RuleDef
from gnomon.kernel.service import INF


class MutationNotApplicable(LookupError):
    """The graph holds no element this mutation can corrupt."""


@dataclass(frozen=True)
class Mutation:
    id: str
    title: str
    expected: tuple[str, ...]
    apply: Callable[[object, redis.Redis, str], None]


def _row(graph, query: str, params: Mapping | None = None, *, what: str) -> list:
    rows = graph.ro_query(query, dict(params or {})).result_set
    if not rows:
        raise MutationNotApplicable(f"no {what} to mutate")
    return rows[0]


def _middle(graph) -> tuple[str, int]:
    """The rewrite half way through the log (never Genesis)."""
    head = _row(graph, "MATCH (h:Head) RETURN h.seq", what="Head")[0]
    if head < 3:
        raise MutationNotApplicable("the log is too short")
    return tuple(_row(graph, "MATCH (r:Rewrite {seq: $s}) RETURN r.id, r.seq", {"s": head // 2 + 1},
                      what="rewrite"))


def _used_rule(graph) -> str:
    return _row(graph, "MATCH (r:Rewrite) WHERE r.rule <> 'Genesis' MATCH (ru:Rule {sha: r.rule_sha}) "
                       "RETURN ru.sha ORDER BY r.seq DESC LIMIT 1", what="rule in use")[0]


def _fact_rels(graph) -> list[str]:
    return [rel for (rel,) in graph.ro_query("MATCH (ft:FactType) RETURN ft.rel ORDER BY ft.rel").result_set]


def _current_owned_by(graph) -> list:
    return _row(graph, "MATCH (s:Entity)-[f:OWNED_BY]->(o:Entity) WHERE f.te = $INF AND f.vt = $INF "
                       "RETURN s.uid, o.uid, f.vf ORDER BY f.fid LIMIT 1", {"INF": INF}, what="current OWNED_BY")


def mu1(graph, client, key):
    rid, _ = _middle(graph)
    graph.query("MATCH (r:Rewrite {id: $id}) SET r.params = r.params + ' '", {"id": rid})


def mu2(graph, client, key):
    rid, _ = _middle(graph)
    graph.query("MATCH (r:Rewrite {id: $id}) SET r.pt = r.pt + 1", {"id": rid})


def mu3(graph, client, key):
    _, seq = _middle(graph)
    graph.query("MATCH (r:Rewrite {seq: $a}) SET r.seq = -1", {"a": seq})
    graph.query("MATCH (r:Rewrite {seq: $b}) SET r.seq = $a", {"a": seq, "b": seq + 1})
    graph.query("MATCH (r:Rewrite {seq: -1}) SET r.seq = $b", {"b": seq + 1})


def mu4(graph, client, key):
    rid, _ = _middle(graph)
    graph.query("MATCH (r:Rewrite {id: $id}) DETACH DELETE r", {"id": rid})


def mu5(graph, client, key):
    graph.query("MATCH (h:Head) SET h.hash = CASE WHEN left(h.hash, 1) = 'f' THEN '0' ELSE 'f' END "
                "+ substring(h.hash, 1)")


def mu6(graph, client, key):
    graph.query("MATCH (ru:Rule {sha: $sha}) SET ru.body = ru.body + '\n// tampered'", {"sha": _used_rule(graph)})


def mu7(graph, client, key):
    forged = RuleDef(name="Forged", version=1, cls="core", layer="core", caller=("kernel",), prelude="P0",
                     body="WITH KCTX\n")
    p = forged.to_param()
    graph.query(
        "MATCH (h:Head) CREATE (:Rule {name: $p.name, version: $p.version, sha: $p.sha, body: $p.body, "
        "prelude: $p.prelude, evidence: $p.evidence, class: $p.class, layer: $p.layer, caller: $p.caller, "
        "reads: $p.reads, writes: $p.writes, key_templates: $p.key_templates, view_templates: $p.view_templates, "
        "guard_probes_json: $p.guard_probes_json, requires_decision: $p.requires_decision, gated: $p.gated, "
        "active: true, registered_seq: h.seq, rw: 'forged'})", {"p": p})


def mu8(graph, client, key):
    graph.query("MATCH (ru:Rule {sha: $sha}) SET ru.gated = NOT coalesce(ru.gated, false)", {"sha": _used_rule(graph)})


def mu9(graph, client, key):
    tid = _row(graph, "MATCH (t:Task) RETURN t.id ORDER BY t.id LIMIT 1", what="Task")[0]
    graph.query("MATCH (t:Task {id: $id}) SET t.result = 'forged out of band'", {"id": tid})


def mu10(graph, client, key):
    s, o, vf = _current_owned_by(graph)
    graph.query("MATCH (h:Head), (s:Entity {uid: $s}), (o:Entity {uid: $o}) "
                "CREATE (s)-[:OWNED_BY {fid: 'mu10#0', vf: $vf + 1, vt: $INF, ts: h.seq, te: $INF}]->(o)",
                {"s": s, "o": o, "vf": vf, "INF": INF})


def mu11(graph, client, key):
    rel, fid = _row(graph, "MATCH ()-[f]->() WHERE type(f) IN $rels RETURN type(f), f.fid ORDER BY f.te, f.fid LIMIT 1",
                    {"rels": _fact_rels(graph)}, what="fact version")
    graph.query(f"MATCH ()-[f:{rel}]->() WHERE f.fid = $fid DELETE f", {"fid": fid})


def mu12(graph, client, key):
    pid = _row(graph, "MATCH (o:Outbox) RETURN o.pid ORDER BY o.pid LIMIT 1", what="Outbox row")[0]
    graph.query("MATCH (o:Outbox {pid: $pid}) SET o.state = CASE WHEN o.state = 'pending' THEN 'delivered' "
                "ELSE 'pending' END", {"pid": pid})


def mu13(graph, client, key):
    tid, holder, until = _row(graph, "MATCH (a:Agent)-[l:HOLDS]->(t:Task) WHERE l.closed_seq IS NULL "
                                     "RETURN t.id, a.id, l.until_pt ORDER BY t.id LIMIT 1", what="open lease")
    other = _row(graph, "MATCH (a:Agent) WHERE a.id <> $h RETURN a.id ORDER BY a.id LIMIT 1", {"h": holder},
                 what="second agent")[0]
    graph.query("MATCH (h:Head), (a:Agent {id: $a}), (t:Task {id: $t}) "
                "CREATE (a)-[:HOLDS {from_seq: h.seq, until_pt: $until}]->(t)", {"a": other, "t": tid, "until": until})


def mu14(graph, client, key):
    s, o, vf = _current_owned_by(graph)
    graph.query("MATCH (h:Head), (s:Entity {uid: $s}), (o:Entity {uid: $o}) "
                "CREATE (s)-[:OWNED_BY {fid: 'mu14#0', vf: $vf + 1, vt: $vf + 2, ts: h.seq, te: $INF}]->(o)",
                {"s": s, "o": o, "vf": vf, "INF": INF})


def mu15(graph, client, key):
    cid, citer = _row(graph, "MATCH (m)-[c:CITES]->() RETURN ID(c), ID(m) ORDER BY ID(c) LIMIT 1", what="citation")
    subject, fid = _row(graph, "MATCH (s:Entity)-[f]->() WHERE type(f) IN $rels AND f.te < $INF "
                               "RETURN s.uid, f.fid ORDER BY f.te, f.fid LIMIT 1",
                        {"rels": _fact_rels(graph), "INF": INF}, what="retracted fact")
    graph.query("MATCH (m)-[c:CITES]->() WHERE ID(c) = $cid AND ID(m) = $m MATCH (s:Entity {uid: $s}) "
                "CREATE (m)-[n:CITES]->(s) SET n = properties(c), n.fid = $fid DELETE c",
                {"cid": cid, "m": citer, "s": subject, "fid": fid})


def mu16(graph, client, key):
    projs = [p for (p,) in graph.ro_query("MATCH (ft:FactType) WHERE ft.projection IS NOT NULL "
                                          "RETURN ft.projection ORDER BY ft.projection").result_set]
    proj, fid = _row(graph, "MATCH ()-[p]->() WHERE type(p) IN $projs RETURN type(p), p.fid ORDER BY type(p), p.fid "
                            "LIMIT 1", {"projs": projs}, what="projection edge")
    graph.query(f"MATCH ()-[p:{proj}]->() WHERE p.fid = $fid DELETE p", {"fid": fid})


def mu17(graph, client, key):
    _middle(graph)
    head = _row(graph, "MATCH (h:Head) RETURN h.seq", what="Head")[0]
    graph.query("MATCH (a:Rewrite {seq: 2}), (b:Rewrite {seq: $s}) CREATE (a)-[:CAUSED_BY]->(b)", {"s": head})


def mu18(graph, client, key):
    client.execute_command("GRAPH.CONSTRAINT", "DROP", key, "MANDATORY", "NODE", "Rewrite", "PROPERTIES", "2", "seq",
                           "rule_sha")
    graph.query("CREATE (:Rewrite {id: 'mu18-ghost'})")


def mu19(graph, client, key):
    cid = _row(graph, "MATCH (c:Candidate) WHERE c.score_num IS NOT NULL RETURN c.cid ORDER BY c.cid LIMIT 1",
               what="scored Candidate")[0]
    graph.query("MATCH (c:Candidate {cid: $cid}) SET c.score_num = c.score_num + 1", {"cid": cid})


def mu20(graph, client, key):
    did = _row(graph, "MATCH (x:Decision) RETURN x.id ORDER BY x.id LIMIT 1", what="Decision")[0]
    graph.query("MATCH (x:Decision {id: $id}) DETACH DELETE x", {"id": did})


def mu21(graph, client, key):
    did = _row(graph, "MATCH (x:Decision) RETURN x.id ORDER BY x.id LIMIT 1", what="Decision")[0]
    graph.query("MATCH (x:Decision {id: $id}) SET x.read_seq = x.read_seq + 1", {"id": did})


MUTATIONS: dict[str, Mutation] = {m.id: m for m in (
    Mutation("MU1", "Edit Rewrite.params", ("K1", "K4"), mu1),
    Mutation("MU2", "Edit Rewrite.pt", ("K1",), mu2),
    Mutation("MU3", "Swap the seq of two rewrites", ("K1",), mu3),
    Mutation("MU4", "Delete a Rewrite", ("K2", "K1"), mu4),
    Mutation("MU5", "Edit Head.hash", ("K1",), mu5),
    Mutation("MU6", "Edit Rule.body, keeping its sha", ("K3",), mu6),
    Mutation("MU7", "Create a Rule out of band", ("K3", "K4"), mu7),
    Mutation("MU8", "Flip Rule.gated", ("K3", "K4"), mu8),
    Mutation("MU9", "SET Task.result out of band", ("K4",), mu9),
    Mutation("MU10", "Create an OWNED_BY edge out of band", ("K4", "K5b", "K5d"), mu10),
    Mutation("MU11", "Delete a fact version", ("K4",), mu11),
    Mutation("MU12", "Edit Outbox.state", ("K4",), mu12),
    Mutation("MU13", "Second unclosed HOLDS on a task", ("K5a", "K4"), mu13),
    Mutation("MU14", "Overlapping current ownership versions", ("K5b", "K4"), mu14),
    Mutation("MU15", "Repoint a CITES.fid to a retracted fact", ("K5c", "K4"), mu15),
    Mutation("MU16", "Delete a DEP_NOW projection", ("K5d", "K4"), mu16),
    Mutation("MU17", "Forward CAUSED_BY edge", ("K6", "K4"), mu17),
    Mutation("MU18", "Ghost Rewrite node with no seq", ("K6", "K2"), mu18),
)}


# T-A rows (§21.2); kept apart so the Core catalogue stays MU1-MU18 until the layer is active.
LAYER_MUTATIONS: dict[str, Mutation] = {m.id: m for m in (
    Mutation("MU19", "Edit Candidate.score_num", ("K7", "K4"), mu19),
    Mutation("MU20", "Delete a Decision", ("K7", "K4"), mu20),
    Mutation("MU21", "Edit Decision.read_seq", ("K7", "K4"), mu21),
)}


def _executive_decision(graph) -> str:
    return _row(graph, "MATCH (x:Decision) WHERE x.scorer ENDS WITH '.blastRadius' RETURN x.scorer ORDER BY x.id LIMIT 1",
                what="Executive decision")[0]


def mu22(graph, client, key):
    """Load a different library under the scorer's name (the library is global to the server, not to the graph)."""
    from gnomon.udf import loader

    library = _executive_decision(graph).rsplit(".", 1)[0]
    code = loader.listed_code(client, library)
    loader.load(client, library, code + "\n// a different library under the same name\n")


def mu23(graph, client, key):
    """Change lambda in the Executive's configuration (in-process: the caller restores it)."""
    from gnomon.executive import scoring

    _executive_decision(graph)
    scoring.LAMBDA_D += 1


# T-B rows (§21.2): configuration and library mutations, so the graph hash does not change.
TB_MUTATIONS: dict[str, Mutation] = {m.id: m for m in (
    Mutation("MU22", "Load a different Exec library under the same name", ("K7",), mu22),
    Mutation("MU23", "Change lambda in the Executive's configuration", ("K7",), mu23),
)}


def _drop_index(graph, entity: str, label: str, prop: str) -> None:
    from gnomon.ops.reconciler import drop_statement

    graph.query(drop_statement(entity, label, prop))


def mu24(graph, client, key):
    """Drop an optimizer index out of band: the log says it is operational, the server has none."""
    entity, label, prop = _row(graph, "MATCH (x:IndexEntry) WHERE x.origin = 'optimizer' AND x.state = 'operational' "
                                      "RETURN x.entity, x.label, x.prop ORDER BY x.key LIMIT 1", what="optimizer index")
    _drop_index(graph, entity, label, prop)


OUT_OF_BAND_INDEXES = (("node", "Service", "tier"), ("node", "Service", "name"), ("node", "Team", "name"),
                       ("edge", "OWNED_BY", "vt"), ("edge", "DEPENDS_ON", "vt"))


def mu25(graph, client, key):
    """Create an index nobody logged and wait until the server reports it operational."""
    import time

    from gnomon.ops.reconciler import create_statement

    logged = {k for (k,) in graph.ro_query("MATCH (x:IndexEntry) RETURN x.key").result_set}
    from gnomon.audit.k8 import physical_indexes

    existing = logged | set(physical_indexes(graph))
    pick = next((i for i in OUT_OF_BAND_INDEXES if ":".join(i) not in existing), None)
    if pick is None:
        raise MutationNotApplicable("every candidate index already exists")
    graph.query(create_statement(*pick))
    while physical_indexes(graph).get(":".join(pick)) != "OPERATIONAL":
        time.sleep(0.05)


# T-C rows (§21.2): the physical catalogue drifts from the logged one, so the graph hash does not change.
TC_MUTATIONS: dict[str, Mutation] = {m.id: m for m in (
    Mutation("MU24", "Drop an optimizer index out of band", ("K8",), mu24),
    Mutation("MU25", "Create an index out of band", ("K8",), mu25),
)}


def apply_mutation(db, client: redis.Redis, key: str, mu: str) -> Mutation:
    """Applies mutation `mu` to graph `key` (a scratch copy) and returns it."""
    mutation = {**MUTATIONS, **LAYER_MUTATIONS, **TB_MUTATIONS, **TC_MUTATIONS}[mu]
    mutation.apply(db.select_graph(key), client, key)
    return mutation
