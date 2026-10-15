"""S17 · algo.SPpaths without weightProp (hop count); algo.bfs direction."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_s17_sppaths_counts_hops_and_bfs_direction(graph, facts):
    q = lambda cypher: graph.query(cypher).result_set
    q(
        "CREATE (a:Service {id: 'a'})-[:DEP_NOW]->(b:Service {id: 'b'})-[:DEP_NOW]->(c:Service {id: 'c'})"
        "-[:DEP_NOW]->(d:Service {id: 'd'}), (a)-[:DEP_NOW]->(d)"
    )
    sp = q(
        "MATCH (a:Service {id: 'a'}), (b:Service {id: 'c'}) CALL algo.SPpaths({sourceNode: a, targetNode: b, "
        "relTypes: ['DEP_NOW'], relDirection: 'outgoing', maxLen: 4, pathCount: 1}) YIELD path, pathWeight "
        "RETURN [n IN nodes(path) | n.id], pathWeight"
    )
    hop_count = sp == [[["a", "b", "c"], 2]]
    reverse = q(
        "MATCH (a:Service {id: 'd'}), (b:Service {id: 'a'}) CALL algo.SPpaths({sourceNode: a, targetNode: b, "
        "relTypes: ['DEP_NOW'], relDirection: 'both', maxLen: 4, pathCount: 1}) YIELD path "
        "RETURN [n IN nodes(path) | n.id]"
    )
    both_directions = reverse == [[["d", "a"]]]

    downstream = q("MATCH (s:Service {id: 'b'}) CALL algo.bfs(s, 3, 'DEP_NOW') YIELD nodes RETURN [n IN nodes | n.id]")
    upstream = q("MATCH (s:Service {id: 'd'}) CALL algo.bfs(s, 3, 'DEP_NOW') YIELD nodes RETURN [n IN nodes | n.id]")
    # Observed: bfs follows outgoing edges only and takes no direction argument.
    assert downstream == [[["c", "d"]]]
    bfs_reaches_upstream = upstream not in ([], [[[]]])

    facts.expect("S17", hop_count and both_directions and bfs_reaches_upstream)
