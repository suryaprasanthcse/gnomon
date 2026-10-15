"""F4 · GRAPH.CONSTRAINT replies PENDING and db.constraints() reaches OPERATIONAL."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_f04_constraint_creation_is_asynchronous(graph, tenant, redis_raw, wait_operational, error_of, facts):
    graph.query("UNWIND range(1, 2000) AS i CREATE (:Rewrite {seq: i, rule_sha: 'x'})")
    without_index = error_of(
        lambda: redis_raw.execute_command("GRAPH.CONSTRAINT", "CREATE", tenant, "UNIQUE", "NODE", "Rewrite", "PROPERTIES", "1", "seq")
    )
    graph.query("CREATE INDEX FOR (n:Rewrite) ON (n.seq)")
    wait_operational(graph)
    unique_reply = redis_raw.execute_command(
        "GRAPH.CONSTRAINT", "CREATE", tenant, "UNIQUE", "NODE", "Rewrite", "PROPERTIES", "1", "seq"
    )
    mandatory_reply = redis_raw.execute_command(
        "GRAPH.CONSTRAINT", "CREATE", tenant, "MANDATORY", "NODE", "Rewrite", "PROPERTIES", "2", "seq", "rule_sha"
    )
    wait_operational(graph)
    rows = sorted(tuple(map(str, r)) for r in graph.query("CALL db.constraints()").result_set)

    assert without_index and "missing supporting exact-match index" in without_index
    facts.expect(
        "F4",
        unique_reply == "PENDING"
        and mandatory_reply == "PENDING"
        and rows
        == [
            ("MANDATORY", "Rewrite", "['seq', 'rule_sha']", "NODE", "OPERATIONAL"),
            ("UNIQUE", "Rewrite", "['seq']", "NODE", "OPERATIONAL"),
        ],
    )
