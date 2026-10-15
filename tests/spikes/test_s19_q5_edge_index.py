"""S19 · Does the unanchored Q5 pattern use Edge By Index Scan once the index exists?"""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]

Q5 = (
    "CYPHER INF=9007199254740991 T1=10 T2=20 MATCH (h:Head) MATCH (s:Service)-[f:OWNED_BY]->(t:Team) "
    "WHERE f.te = $INF AND f.vf >= $T1 AND f.vf < $T2 "
    "RETURN h.seq AS read_seq, s.id AS service, t.id AS team, f.vf AS changed_at, f.fid AS fid ORDER BY changed_at"
)


def test_s19_q5_switches_to_edge_by_index_scan(graph, tenant, redis_raw, wait_operational, facts):
    graph.query(
        "UNWIND range(1, 2000) AS i CREATE (:Service {id: 's' + toString(i)})"
        "-[:OWNED_BY {fid: 'f' + toString(i), te: 9007199254740991, vf: i}]->(:Team {id: 't' + toString(i % 8)})"
    )
    graph.query("CREATE (:Head {seq: 1})")
    explain = lambda: "\n".join(redis_raw.execute_command("GRAPH.EXPLAIN", tenant, Q5))
    before = explain()
    graph.query("CREATE INDEX FOR ()-[f:OWNED_BY]-() ON (f.vf)")
    wait_operational(graph)
    after = explain()
    rows = graph.query(Q5).result_set

    facts.expect("S19", "Edge By Index Scan" not in before and "Edge By Index Scan" in after and len(rows) == 10)
