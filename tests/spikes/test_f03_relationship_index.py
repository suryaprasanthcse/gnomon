"""F3 · Relationship-property index syntax, DROP INDEX ON :R(p), and Edge By Index Scan in EXPLAIN."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def _indexes(graph):
    return sorted((r[0], tuple(r[1]), r[6]) for r in graph.query("CALL db.indexes()").result_set)


def test_f03_relationship_index_create_explain_and_drop(graph, tenant, redis_raw, wait_operational, error_of, facts):
    graph.query("UNWIND range(1, 300) AS i CREATE (:S {id: i})-[:OWNED_BY {vf: i}]->(:T {id: i})")
    graph.query("CREATE INDEX FOR ()-[f:OWNED_BY]-() ON (f.vf)")
    graph.query("CREATE INDEX FOR (n:S) ON (n.id)")
    wait_operational(graph)
    created = _indexes(graph) == [("OWNED_BY", ("vf",), "RELATIONSHIP"), ("S", ("id",), "NODE")]
    plan = redis_raw.execute_command("GRAPH.EXPLAIN", tenant, "MATCH (s:S)-[f:OWNED_BY]->(t:T) WHERE f.vf >= 10 RETURN count(f)")
    edge_scan = any("Edge By Index Scan" in line for line in plan)

    # The spec's drop form. Observed: it rejects relationship indexes ("no such index").
    on_form_error = error_of(lambda: graph.query("DROP INDEX ON :OWNED_BY(vf)"))
    on_form_drops_relationship = on_form_error is None
    # The form that works for relationships on this image, and the ON form for node indexes.
    graph.query("DROP INDEX FOR ()-[f:OWNED_BY]-() ON (f.vf)")
    graph.query("DROP INDEX ON :S(id)")
    all_dropped = _indexes(graph) == []

    assert on_form_error is None or "no such index" in on_form_error
    assert all_dropped
    facts.expect("F3", created and edge_scan and on_form_drops_relationship)
