"""S14 · Does MEMORY USAGE report graph-key memory? (private server)"""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_s14_memory_usage_of_a_graph_key(private_falkordb, build_log, facts):
    client = private_falkordb.client
    graph = private_falkordb.graph()
    build_log(graph, 64_000)
    try:
        key_bytes = client.execute_command("MEMORY", "USAGE", graph.name)
        report = client.execute_command("GRAPH.MEMORY", "USAGE", graph.name)
    finally:
        graph.delete()

    # Observed: MEMORY USAGE reports only the key's handle (tens of bytes). GRAPH.MEMORY USAGE
    # reports the graph in whole megabytes, broken down by matrices, attributes and indices.
    graph_report = dict(zip(report[0::2], report[1::2]))
    assert "total_graph_sz_mb" in graph_report and "indices_sz_mb" in graph_report
    assert graph_report["total_graph_sz_mb"] >= 1
    facts.expect("S14", key_bytes > 1_000_000)
