"""S4 · Does an error mid-query leave no partial writes?"""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_s04_errors_after_writes_roll_the_whole_query_back(
    graph, tenant, redis_raw, load_udf, error_of, wait_operational, facts
):
    lib = load_udf("spike_s04.js", "SpikeS04")
    markers = lambda: graph.query("MATCH (m:Marker) RETURN count(m)").result_set[0][0]

    udf_after_create = error_of(lambda: graph.query(f"CREATE (:Marker) WITH 1 AS x RETURN {lib}.boom()"))
    udf_inside_set = error_of(lambda: graph.query(f"CREATE (m:Marker) SET m.x = {lib}.boom()"))
    after_udf_errors = markers()

    graph.query("CREATE INDEX FOR (k:KV) ON (k.key)")
    wait_operational(graph)
    redis_raw.execute_command("GRAPH.CONSTRAINT", "CREATE", tenant, "UNIQUE", "NODE", "KV", "PROPERTIES", "1", "key")
    redis_raw.execute_command("GRAPH.CONSTRAINT", "CREATE", tenant, "MANDATORY", "NODE", "Head", "PROPERTIES", "1", "seq")
    wait_operational(graph)
    graph.query("CREATE (:KV {key: 'k1'})")
    unique_violation = error_of(lambda: graph.query("CREATE (:Marker) CREATE (:KV {key: 'k1'})"))
    mandatory_violation = error_of(lambda: graph.query("CREATE (:Marker) CREATE (:Head {x: 1})"))
    after_constraint_errors = markers()

    assert udf_after_create and "BOOM" in udf_after_create
    assert unique_violation and "unique constraint violation" in unique_violation
    assert mandatory_violation and "mandatory constraint violation" in mandatory_violation
    facts.expect(
        "S4",
        all([udf_after_create, udf_inside_set, unique_violation, mandatory_violation])
        and after_udf_errors == 0
        and after_constraint_errors == 0,
    )
