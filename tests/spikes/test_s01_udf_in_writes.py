"""S1 · Can UDFs be called in WHERE and SET inside write queries, and return maps and lists?"""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_s01_udfs_in_where_and_set_of_write_queries(graph, load_udf, error_of, facts):
    lib = load_udf("spike_s01.js", "SpikeS01")
    q = lambda cypher: graph.query(cypher).result_set

    returns_map_and_list = q(f"WITH {lib}.aMap() AS m RETURN m.a, m.b[1], m.c, {lib}.aList()") == [
        [1, 2, "x", [1, "two", None]]
    ]
    usable_in_set = q(f"CREATE (n:N {{v: 1}}) SET n.h = {lib}.aMap().c, n.l = {lib}.aList()[0..2] RETURN n.h, n.l") == [
        ["x", [1, "two"]]
    ]
    usable_in_where = q(f"MATCH (n:N) WHERE {lib}.isOne(n.v) SET n.w = 7 RETURN n.w") == [[7]]
    takes_node_argument = q(f"MATCH (n:N) SET n.lab = {lib}.label(n) RETURN n.lab") == [["N"]]

    # Observed limit: a map, or a list holding null, cannot be stored as a property value.
    map_error = error_of(lambda: graph.query(f"MATCH (n:N) SET n.m = {lib}.aMap()"))
    null_list_error = error_of(lambda: graph.query(f"MATCH (n:N) SET n.l2 = {lib}.aList()"))
    assert map_error and "primitive types" in map_error
    assert null_list_error and "primitive types" in null_list_error

    facts.expect("S1", returns_map_and_list and usable_in_set and usable_in_where and takes_node_argument)
