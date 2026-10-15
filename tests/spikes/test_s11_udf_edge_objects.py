"""S11 · Edge.source / Edge.target types and getNeighbors returnType: 'edges'."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_s11_edges_carry_full_endpoint_nodes(graph, load_udf, facts):
    lib = load_udf("spike_f07.js", "SpikeS11")
    graph.query(
        "CREATE (a:Svc {id: 'a'})-[:DEP {fid: '1'}]->(:Svc {id: 'b'}), (a)-[:DEP {fid: '2'}]->(:Svc {id: 'c'})"
    )
    edges = graph.query(f"MATCH (a:Svc {{id: 'a'}}) RETURN {lib}.outEdges(a, 'DEP')").result_set[0][0]
    observed = sorted((dict(e) for e in edges), key=lambda e: e["fid"])
    expected = [
        {"fid": "1", "type": "DEP", "sourceKind": "object", "source": "a", "target": "b", "sourceHasNeighbors": True},
        {"fid": "2", "type": "DEP", "sourceKind": "object", "source": "a", "target": "c", "sourceHasNeighbors": True},
    ]
    facts.expect("S11", observed == expected)
