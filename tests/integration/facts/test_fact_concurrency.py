"""50 parallel identical AssertFact.OWNED_BY proposals: exactly one applies (platform fact S3)."""

from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = [pytest.mark.concurrency, pytest.mark.gate_g1c]

N = 50
INF = 9007199254740991
EPOCH = 1_791_000_000_000


@pytest.mark.parametrize("with_view", [True, False], ids=["view-keys", "no-view-keys"])
def test_50_parallel_identical_asserts(world, falkordb_container, with_view):
    from falkordb import FalkorDB

    from gnomon.kernel.service import KernelHandle

    name, registry = world.k.graph.name, world.k.registry
    read_seq = world.head()["seq"]
    view = ["OWNED_BY:svc:a:*"] if with_view else []

    def submit(i: int):
        client = FalkorDB(host=falkordb_container.host, port=falkordb_container.port)
        try:
            k = KernelHandle(client.select_graph(name), registry)
            return k.submit("AssertFact.OWNED_BY", {"s": "svc:a", "o": "team:t1", "vf": EPOCH + 100, "supersede": False},
                            pid=f"par-{i:02d}", agent="ops-1", read_seq=read_seq, view_keys=view)
        finally:
            client.connection.close()

    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = [o.code for o in pool.map(submit, range(N))]

    assert codes.count("APPLIED") == 1
    allowed = {"REJECTED_STALE", "REJECTED_GUARD"} if with_view else {"REJECTED_GUARD"}
    assert set(codes) - {"APPLIED"} <= allowed
    assert len(world.current("OWNED_BY")) == 1
    assert len(world.projection("OWNER_NOW")) == 1
    assert world.head()["seq"] == read_seq + 1
