"""F1 · GRAPH.COPY snapshot isolation; the copy also carries indexes and constraints (patch P3 relies on it)."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_f01_copy_is_an_isolated_snapshot_with_its_schema(db, graph, tenant, redis_raw, wait_operational, error_of, facts):
    graph.query("UNWIND range(1, 1000) AS i CREATE (:KV {key: 'k' + toString(i)})")
    graph.query("CREATE INDEX FOR (n:KV) ON (n.key)")
    wait_operational(graph)
    redis_raw.execute_command("GRAPH.CONSTRAINT", "CREATE", tenant, "UNIQUE", "NODE", "KV", "PROPERTIES", "1", "key")
    wait_operational(graph)

    dest_key = f"{tenant}:sim:f1"
    assert redis_raw.execute_command("GRAPH.COPY", tenant, dest_key) == "OK"
    graph.query("CREATE (:AfterCopy)")
    dest = db.select_graph(dest_key)

    isolated = dest.query("MATCH (n:AfterCopy) RETURN count(n)").result_set == [[0]]
    complete = dest.query("MATCH (n:KV) RETURN count(n)").result_set == [[1000]]
    dest_indexes = [(r[0], r[1], r[7]) for r in dest.query("CALL db.indexes()").result_set]
    dest_constraints = [(r[0], r[1], r[4]) for r in dest.query("CALL db.constraints()").result_set]
    schema_carried = dest_indexes == [("KV", ["key"], "OPERATIONAL")] and dest_constraints == [("UNIQUE", "KV", "OPERATIONAL")]
    enforced = error_of(lambda: dest.query("CREATE (:KV {key: 'k1'})"))
    overwrite = error_of(lambda: redis_raw.execute_command("GRAPH.COPY", tenant, dest_key))

    assert overwrite and "already exists" in overwrite  # a copy never replaces an existing key
    facts.expect("F1", isolated and complete and schema_carried and enforced is not None)
