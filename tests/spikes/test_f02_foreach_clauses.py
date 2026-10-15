"""F2 · FOREACH accepts CREATE / MERGE / SET / REMOVE / DELETE / nested FOREACH and sees outer variables."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_f02_foreach_clauses_and_outer_variables(graph, facts):
    q = lambda cypher: graph.query(cypher).result_set
    q("CREATE (:H {tag: 'h'})")
    q("MATCH (h:H) FOREACH (x IN [1, 2] | CREATE (:C {v: x, tag: h.tag}))")
    q("MATCH (h:H) FOREACH (x IN [1, 1, 2] | MERGE (m:M {v: x}) CREATE (h)-[:TO]->(m))")
    q("MATCH (h:H) FOREACH (x IN [h] | SET x.s = 1, x.t = 2 REMOVE x.t)")
    q("MATCH (h:H) FOREACH (a IN [1, 2] | FOREACH (b IN [3] | CREATE (:N {v: a * b, tag: h.tag})))")
    created = q("MATCH (c:C {tag: 'h'}) RETURN count(c)") == [[2]]
    merged = q("MATCH (m:M) RETURN count(m)") == [[2]] and q("MATCH (:H)-[t:TO]->() RETURN count(t)") == [[3]]
    set_remove = q("MATCH (h:H) RETURN h.s, h.t") == [[1, None]]
    nested = q("MATCH (n:N {tag: 'h'}) RETURN n.v ORDER BY n.v") == [[3], [6]]
    q("MATCH (:H)-[t:TO]->() WITH collect(t) AS ts FOREACH (x IN ts | DELETE x)")
    deleted = q("MATCH ()-[t:TO]->() RETURN count(t)") == [[0]]

    facts.expect("F2", created and merged and set_remove and nested and deleted)
