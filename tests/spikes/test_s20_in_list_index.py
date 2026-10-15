"""S20 · Is `kv.key IN $list` / `uid IN $list` served by the index?"""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_s20_in_list_predicates_use_node_index_scan(graph, tenant, redis_raw, wait_operational, facts):
    graph.query("UNWIND range(1, 500) AS i CREATE (:KeyVersion {key: 'k' + toString(i), seq: i})")
    graph.query("UNWIND range(1, 500) AS i CREATE (:Entity {uid: 'svc:' + toString(i)})")
    graph.query("CREATE (:Head {seq: 1})")
    graph.query("CREATE INDEX FOR (n:KeyVersion) ON (n.key)")
    graph.query("CREATE INDEX FOR (n:Entity) ON (n.uid)")
    wait_operational(graph)

    prelude_shape = (
        "CYPHER keys=['k1','k2'] MATCH (h:Head) OPTIONAL MATCH (kv:KeyVersion) WHERE kv.key IN $keys "
        "WITH h, sum(CASE WHEN kv.seq > 1 THEN 1 ELSE 0 END) AS stale RETURN stale"
    )
    uid_shape = "CYPHER uids=['svc:1','svc:2'] MATCH (e:Entity) WHERE e.uid IN $uids RETURN e.uid"
    plans = ["\n".join(redis_raw.execute_command("GRAPH.EXPLAIN", tenant, q)) for q in (prelude_shape, uid_shape)]
    stale = graph.query(prelude_shape).result_set

    facts.expect("S20", all("Node By Index Scan" in p for p in plans) and stale == [[1]])
