"""S6 · Index status values and build polling."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_s06_index_build_is_polled_until_operational(graph, tenant, redis_raw, wait_operational, facts):
    graph.query("UNWIND range(1, 20000) AS i CREATE (:Svc {id: i})")
    graph.query("CREATE INDEX FOR (n:Svc) ON (n.id)")
    seen = wait_operational(graph)

    # Observed values: "[Indexing] <done>/<total>: UNDER CONSTRUCTION" while building, then "OPERATIONAL".
    known_values = all(s == "OPERATIONAL" or s.endswith(": UNDER CONSTRUCTION") for s in seen)
    plan = redis_raw.execute_command("GRAPH.EXPLAIN", tenant, "MATCH (n:Svc) WHERE n.id = 7 RETURN n")
    uses_index = any("Node By Index Scan" in line for line in plan)

    facts.expect("S6", known_values and seen[-1] == "OPERATIONAL" and uses_index)
