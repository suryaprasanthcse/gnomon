"""Concurrent proposals (platform fact S3): 50 parallel Noops with distinct pids all apply, gapless and chained."""

from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = [pytest.mark.concurrency, pytest.mark.gate_g1b]

N = 50


def test_50_parallel_noop_distinct_pids(genesis, falkordb_container, ref_chain, chain_of):
    from falkordb import FalkorDB

    from gnomon.kernel.service import KernelHandle

    name = genesis.graph.name

    def submit(i: int):
        client = FalkorDB(host=falkordb_container.host, port=falkordb_container.port)
        try:
            handle = KernelHandle(client.select_graph(name), genesis.registry)
            return handle.submit("Noop", {"note": f"n{i}", "key": f"k{i}"}, pid=f"par-{i:02d}", read_seq=1,
                                 view_keys=["Head"])
        finally:
            client.connection.close()

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(submit, range(N)))

    assert [o.code for o in outcomes] == ["APPLIED"] * N
    assert sorted(o.seq for o in outcomes) == list(range(2, N + 2))
    head = genesis.head()
    assert head["seq"] == N + 1

    log = chain_of(genesis.graph)
    assert [r["seq"] for r in log] == list(range(1, N + 2))
    assert sorted(r["id"] for r in log[1:]) == [f"par-{i:02d}" for i in range(N)]
    for prev, r in zip(log, log[1:]):
        assert r["prev"] == prev["hash"]
        assert r["hash"] == ref_chain(r)
        assert (r["pt"], r["lc"]) > (prev["pt"], prev["lc"])
    assert head["hash"] == log[-1]["hash"]
    assert genesis.read("MATCH (n:Noop) RETURN count(n)") == [[N]]
