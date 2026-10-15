"""S2 · FOREACH (_ IN CASE …), nested FOREACH with MERGE, DELETE inside FOREACH."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_s02_conditional_nested_and_deleting_foreach(graph, facts):
    q = lambda cypher: graph.query(cypher).result_set
    q("CREATE (:H)")

    q("MATCH (h:H) FOREACH (_ IN CASE WHEN true THEN [1] ELSE [] END | CREATE (:Yes))")
    q("MATCH (h:H) FOREACH (_ IN CASE WHEN false THEN [1] ELSE [] END | CREATE (:No))")
    conditional = q("MATCH (y:Yes) RETURN count(y)") == [[1]] and q("MATCH (n:No) RETURN count(n)") == [[0]]

    q("MATCH (h:H) FOREACH (a IN [1, 2] | FOREACH (b IN [10, 20] | MERGE (k:K {k: a * 100 + b}) CREATE (h)-[:E]->(k)))")
    q("MATCH (h:H) FOREACH (a IN [1, 2] | FOREACH (b IN [10, 20] | MERGE (k:K {k: a * 100 + b})))")  # idempotent
    nested_merge = q("MATCH (k:K) RETURN count(k)") == [[4]] and q("MATCH (:H)-[e:E]->(:K) RETURN count(e)") == [[4]]

    q("MATCH (:H)-[e:E]->() WITH collect(e) AS es FOREACH (x IN es | DELETE x)")
    q("MATCH (k:K) WITH collect(k) AS ks FOREACH (x IN ks | DELETE x)")
    deleting = q("MATCH ()-[e:E]->() RETURN count(e)") == [[0]] and q("MATCH (k:K) RETURN count(k)") == [[0]]

    facts.expect("S2", conditional and nested_merge and deleting)
