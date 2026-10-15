"""S15 · Label predicates in WHERE; *0..4; null properties omitted on CREATE; toString, startNode,
endNode, properties; UNION."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_s15_cypher_constructs_used_by_the_templates(graph, facts):
    q = lambda cypher: graph.query(cypher).result_set
    q("CREATE (:L1 {v: 1}), (:L2 {v: 2})")
    q("CREATE (:C {i: 0})-[:R {w: 1}]->(:C {i: 1})-[:R {w: 2}]->(:C {i: 2})")

    checks = {
        "label predicate": q("MATCH (n) WHERE n:L1 RETURN n.v") == [[1]],
        "var-length *0..4": q("MATCH (:C {i: 0})-[:R*0..4]->(b) RETURN b.i ORDER BY b.i") == [[0], [1], [2]],
        "null omitted on CREATE": q("CREATE (n:Nul {a: null, b: 1}) RETURN keys(n)") == [[["b"]]],
        "null map field omitted": q("UNWIND [{k: 1, r: null}] AS cd CREATE (n:Nul2 {k: cd.k, r: cd.r}) RETURN keys(n)")
        == [[["k"]]],
        "toString/startNode/endNode": q("MATCH (a:C {i: 0})-[r:R]->() RETURN toString(a.i), startNode(r).i, endNode(r).i")
        == [["0", 0, 1]],
        "properties": [dict(x[0]) for x in q("MATCH (:C {i: 0})-[r:R]->() RETURN properties(r)")] == [{"w": 1}],
        "UNION": q("MATCH (n:L1) RETURN n.v AS v UNION MATCH (n:L2) RETURN n.v AS v") == [[1], [2]],
    }
    failed = [name for name, ok in checks.items() if not ok]
    print(f"S15 unsupported constructs: {failed}")
    facts.expect("S15", not failed)
