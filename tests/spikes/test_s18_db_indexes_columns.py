"""S18 · db.indexes() YIELD column names and status values."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]

COLUMNS = ["label", "properties", "types", "options", "language", "stopwords", "entitytype", "status", "info"]


def test_s18_db_indexes_yields_named_columns(graph, wait_operational, facts):
    graph.query("CREATE (:Svc {id: 1})-[:R {fid: 'f'}]->(:Svc {id: 2})")
    graph.query("CREATE INDEX FOR (n:Svc) ON (n.id)")
    graph.query("CREATE INDEX FOR ()-[f:R]-() ON (f.fid)")
    wait_operational(graph)

    columns = [h[1] for h in graph.query("CALL db.indexes()").header]
    rows = graph.query(
        "CALL db.indexes() YIELD label, properties, types, entitytype, status "
        "RETURN label, properties, types, entitytype, status ORDER BY label"
    ).result_set
    observed = [(r[0], r[1], dict(r[2]), r[3], r[4]) for r in rows]
    expected = [
        ("R", ["fid"], {"fid": ["RANGE"]}, "RELATIONSHIP", "OPERATIONAL"),
        ("Svc", ["id"], {"id": ["RANGE"]}, "NODE", "OPERATIONAL"),
    ]
    facts.expect("S18", columns == COLUMNS and observed == expected)
