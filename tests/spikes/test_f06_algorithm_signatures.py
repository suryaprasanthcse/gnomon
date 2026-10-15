"""F6 · algo.* signatures: pageRank, betweenness, WCC, labelPropagation, bfs, SPpaths, maxFlow (master §10.5)."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_f06_algorithm_signatures_used_by_the_tool_plane(graph, facts):
    q = lambda cypher: graph.query(cypher).result_set
    q(
        "CREATE (a:Service {id: 'a'})-[:DEP_NOW {fid: '1', cap: 5}]->(b:Service {id: 'b'})"
        "-[:DEP_NOW {fid: '2', cap: 5}]->(c:Service {id: 'c'})-[:DEP_NOW {fid: '3', cap: 5}]->(d:Service {id: 'd'}), "
        "(a)-[:DEP_NOW {fid: '4', cap: 5}]->(d), (:Service {id: 'e'})-[:DEP_NOW {fid: '5', cap: 5}]->(:Service {id: 'f'})"
    )
    checks = {
        "pageRank": len(q("CALL algo.pageRank('Service', 'DEP_NOW') YIELD node, score RETURN node.id, score")) == 6,
        "betweenness": q(
            "CALL algo.betweenness({nodeLabels: ['Service'], relationshipTypes: ['DEP_NOW'], samplingSize: 10, "
            "samplingSeed: 7}) YIELD node, score RETURN node.id, score ORDER BY node.id"
        )[1:3]
        == [["b", 1.0], ["c", 1.0]],
        "WCC": sorted(
            sorted(x[0])
            for x in q(
                "CALL algo.WCC({nodeLabels: ['Service'], relationshipTypes: ['DEP_NOW']}) YIELD node, componentId "
                "RETURN collect(node.id), componentId"
            )
        )
        == [["a", "b", "c", "d"], ["e", "f"]],
        "labelPropagation": len(
            q(
                "CALL algo.labelPropagation({nodeLabels: ['Service'], relationshipTypes: ['DEP_NOW'], maxIterations: 10}) "
                "YIELD node, communityId RETURN node.id, communityId"
            )
        )
        == 6,
        "bfs": q(
            "MATCH (s:Service {id: 'b'}) CALL algo.bfs(s, 3, 'DEP_NOW') YIELD nodes, edges "
            "RETURN [n IN nodes | n.id], [e IN edges | e.fid]"
        )
        == [[["c", "d"], ["2", "3"]]],
        "SPpaths": q(
            "MATCH (a:Service {id: 'a'}), (b:Service {id: 'd'}) CALL algo.SPpaths({sourceNode: a, targetNode: b, "
            "relTypes: ['DEP_NOW'], relDirection: 'both', maxLen: 4, pathCount: 1}) YIELD path, pathWeight, pathCost "
            "RETURN [n IN nodes(path) | n.id], pathWeight"
        )
        == [[["a", "d"], 1]],
        # Observed signature: {sourceNodes, targetNodes, capacityProperty, relationshipTypes}
        # YIELD nodes, edges, edgeFlows, maxFlow.
        "maxFlow": q(
            "MATCH (a:Service {id: 'a'}), (b:Service {id: 'd'}) CALL algo.maxFlow({sourceNodes: [a], targetNodes: [b], "
            "capacityProperty: 'cap', relationshipTypes: ['DEP_NOW']}) YIELD nodes, edges, edgeFlows, maxFlow "
            "RETURN maxFlow, size(edges)"
        )
        == [[10.0, 4]],
    }
    failed = [name for name, ok in checks.items() if not ok]
    print(f"F6 signatures that differ: {failed}")
    facts.expect("F6", not failed)
