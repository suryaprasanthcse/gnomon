"""50 parallel ClaimTask proposals on one task (master §7.6 R5 × R5, I6): exactly one wins."""

from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = [pytest.mark.concurrency, pytest.mark.gate_g2b]

CLAIMANTS = ["inv-1", "rsp-1", "tri-2"]


def test_50_parallel_claims_one_winner(opened, falkordb_container):
    from falkordb import FalkorDB

    from gnomon.kernel.service import KernelHandle

    ir = opened
    name, registry = ir.k.graph.name, ir.k.registry

    def claim(i: int):
        agent = CLAIMANTS[i % len(CLAIMANTS)]
        client = FalkorDB(host=falkordb_container.host, port=falkordb_container.port)
        try:
            k = KernelHandle(client.select_graph(name), registry)
            # live mode: the server serializes the applies; the HLC orders them
            return k.submit("ClaimTask", {"agent": agent, "task": "t1", "ttl_ms": 60_000}, pid=f"claim-{i:02d}",
                            agent=agent)
        finally:
            client.connection.close()

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(claim, range(50)))

    codes = [o.code for o in outcomes]
    assert codes.count("APPLIED") == 1
    assert codes.count("REJECTED_GUARD") == 49
    assert {o.detail["guard"] for o in outcomes if o.code == "REJECTED_GUARD"} <= {"G3:task_open",
                                                                                     "G6:no_live_lease"}
    assert ir.k.read("MATCH (:Agent)-[l:HOLDS]->(:Task {id: 't1'}) WHERE l.closed_seq IS NULL RETURN count(l)") == \
        [[1]]
    assert ir.one("MATCH (t:Task {id: 't1'}) RETURN t.state") == ["claimed"]
