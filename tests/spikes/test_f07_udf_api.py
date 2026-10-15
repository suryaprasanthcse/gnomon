"""F7 · UDF API: falkor.register, getNeighbors, graph.getNodeById, graph.traverse, iterate*; UDFs cannot write."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_f07_udf_graph_api_is_read_only(graph, redis_raw, load_udf, facts):
    lib = load_udf("spike_f07.js", "SpikeF07")
    q = lambda cypher: graph.query(cypher).result_set
    q(
        "CREATE (a:Service {id: 'a'})-[:DEP_NOW {fid: '1'}]->(b:Service {id: 'b'})-[:DEP_NOW {fid: '2'}]->(c:Service {id: 'c'}), "
        "(a)-[:DEP_NOW {fid: '3'}]->(c)"
    )
    listing = repr(redis_raw.execute_command("GRAPH.UDF", "LIST", "SpikeF07"))
    registered = "SpikeF07" in listing and "tryWrite" in listing
    by_id = q(f"MATCH (s:Service {{id: 'b'}}) RETURN {lib}.attrOf({lib}.nodeId(s), 'id'), {lib}.attrOf(987654, 'id')")
    incoming = q(f"MATCH (s:Service {{id: 'c'}}) RETURN {lib}.inNodes(s, 'DEP_NOW')")
    # Observed: traverse returns one list per start node holding its direct neighbours; `distance` is ignored.
    traversed = q(f"MATCH (s:Service {{id: 'a'}}) RETURN {lib}.traverseIds(s, 'DEP_NOW', 2)")
    iterated = q(f"RETURN {lib}.countAll('Service', 'DEP_NOW')")
    q(f"MATCH (s:Service {{id: 'a'}}) RETURN {lib}.tryWrite(s)")
    unchanged = q("MATCH (s:Service {id: 'a'}) RETURN s.id, s.extra") == [["a", None]]

    assert traversed == [[[["b", "c"]]]]
    facts.expect(
        "F7",
        registered
        and by_id == [["b", None]]
        and sorted(incoming[0][0]) == ["a", "b"]
        and iterated == [[[3, 3]]]
        and unchanged,
    )
