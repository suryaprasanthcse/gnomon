"""S3 · Are writes to one graph serialized?"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from falkordb import FalkorDB

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_s03_concurrent_writes_to_one_graph_are_serialized(falkordb_container, graph, tenant, facts):
    graph.query("CREATE (:Counter {v: 0}), (:Head {seq: 0})")

    def client():
        return FalkorDB(host=falkordb_container.host, port=falkordb_container.port).select_graph(tenant)

    def increment(_):
        g = client()
        for _ in range(20):
            g.query("MATCH (c:Counter) SET c.v = c.v + 1")

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(increment, range(16)))
    counter = graph.query("MATCH (c:Counter) RETURN c.v").result_set[0][0]

    def compare_and_set(_):
        rows = client().query("MATCH (h:Head) WITH h WHERE h.seq = 0 SET h.seq = h.seq + 1 RETURN h.seq").result_set
        return len(rows)

    with ThreadPoolExecutor(50) as pool:
        winners = sum(pool.map(compare_and_set, range(50)))
    final_seq = graph.query("MATCH (h:Head) RETURN h.seq").result_set[0][0]

    facts.expect("S3", counter == 320 and winners == 1 and final_seq == 1)
