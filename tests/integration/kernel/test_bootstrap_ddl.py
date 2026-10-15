"""Bootstrap DDL (master §3.5): every index and constraint is created and reaches OPERATIONAL before R0."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1b]

NODE_INDEXES = {
    ("Head", "tenant"), ("Rule", "name"), ("Rule", "version"), ("Rule", "sha"), ("KeyVersion", "key"),
    ("Rewrite", "id"), ("Rewrite", "seq"), ("FactType", "rel"), ("Entity", "uid"), ("Agent", "id"), ("Skill", "id"),
    ("Team", "id"), ("Service", "id"), ("Deploy", "id"), ("Event", "source_id"), ("Alert", "id"), ("Incident", "id"),
    ("Incident", "service"), ("Task", "id"), ("Message", "id"), ("Outbox", "pid"), ("Outbox", "state"),
    ("IndexEntry", "key"), ("ConstraintEntry", "key"),
    ("Scorer", "name"), ("Scorer", "sha"), ("Approval", "aid"), ("Decision", "id"), ("Candidate", "cid"),
    ("Evidence", "eid"),
    ("Channel", "key"), ("XMsg", "id"), ("XMsg", "state"), ("ExternalService", "remote_id"),
}
FACT_RELS = ["OWNED_BY", "DEPENDS_ON", "DEPLOYED", "REPLICA_OF", "MEMBER_OF", "ROOT_CAUSE", "CONSUMES"]
EDGE_INDEXES = {(r, p) for r in FACT_RELS for p in ("fid", "te")} | {("DEP_NOW", "fid"), ("OWNER_NOW", "fid"),
                                                                       ("CONSUMES_NOW", "fid")}
UNIQUE_NODE = [
    ("Head", ("tenant",)), ("Rule", ("sha",)), ("Rule", ("name", "version")), ("KeyVersion", ("key",)),
    ("Rewrite", ("id",)), ("Rewrite", ("seq",)), ("FactType", ("rel",)), ("Entity", ("uid",)),
    ("Event", ("source_id",)), ("Outbox", ("pid",)), ("IndexEntry", ("key",)), ("ConstraintEntry", ("key",)),
    ("Scorer", ("sha",)), ("Approval", ("aid",)), ("Decision", ("id",)), ("Candidate", ("cid",)),
    ("Evidence", ("eid",)), ("Channel", ("key",)), ("XMsg", ("id",)),
]
CONSTRAINTS = (
    {("UNIQUE", "NODE", label, props) for label, props in UNIQUE_NODE}
    | {("MANDATORY", "NODE", "Rewrite", ("seq", "rule_sha"))}
    | {("UNIQUE", "RELATIONSHIP", rel, ("fid",)) for rel in FACT_RELS}
)


def physical(graph):
    idx = graph.query("CALL db.indexes() YIELD label, properties, entitytype, status RETURN label, properties, "
                      "entitytype, status").result_set
    cons = graph.query("CALL db.constraints() YIELD type, label, properties, entitytype, status RETURN type, label, "
                       "properties, entitytype, status").result_set
    indexes = {(etype, label, prop) for label, props, etype, _ in idx for prop in props}
    constraints = {(ctype, etype, label, tuple(props)) for ctype, label, props, etype, _ in cons}
    statuses = [row[3] for row in idx] + [row[4] for row in cons]
    return indexes, constraints, statuses


def test_bootstrap_ddl(db, redis_raw, tenant):
    from gnomon.bootstrap import ddl

    graph = db.select_graph(tenant)
    ddl.apply(graph, redis_raw)
    indexes, constraints, statuses = physical(graph)
    assert indexes == {("NODE", l, p) for l, p in NODE_INDEXES} | {("RELATIONSHIP", r, p) for r, p in EDGE_INDEXES}
    assert len(indexes) == 51
    assert constraints == CONSTRAINTS
    assert len(constraints) == 27
    assert statuses and all(s == "OPERATIONAL" for s in statuses)


def test_catalogue_matches_the_spec():
    from gnomon.bootstrap import ddl

    assert {(i.entity, i.label, i.prop) for i in ddl.BASE_INDEXES} == (
        {("node", l, p) for l, p in NODE_INDEXES} | {("edge", r, p) for r, p in EDGE_INDEXES}
    )
    assert all(i.key == f"{i.entity}:{i.label}:{i.prop}" for i in ddl.BASE_INDEXES)
    assert {(c.kind, c.entity, c.label, c.props) for c in ddl.BASE_CONSTRAINTS} == {
        (kind.lower(), "node" if etype == "NODE" else "edge", label, props) for kind, etype, label, props in CONSTRAINTS
    }
    assert all(c.key == f"{c.kind}:{c.entity}:{c.label}:{','.join(c.props)}" for c in ddl.BASE_CONSTRAINTS)


def test_apply_is_idempotent(db, redis_raw, tenant):
    from gnomon.bootstrap import ddl

    graph = db.select_graph(tenant)
    ddl.apply(graph, redis_raw)
    first = physical(graph)
    ddl.apply(graph, redis_raw)
    assert physical(graph) == first


def test_operational_catalogue_reads_the_server(db, redis_raw, tenant):
    from gnomon.bootstrap import ddl

    graph = db.select_graph(tenant)
    ddl.apply(graph, redis_raw)
    indexes, constraints = ddl.physical_catalogue(graph)
    assert indexes == {i.key for i in ddl.BASE_INDEXES}
    assert constraints == {c.key for c in ddl.BASE_CONSTRAINTS}
