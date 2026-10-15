"""S13 · Is memory reclaimed after many GRAPH.DELETE calls? (private server)"""

import time

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]

COPIES = 50


def test_s13_memory_returns_after_deleting_scratch_copies(private_falkordb, facts):
    client = private_falkordb.client
    used = lambda: int(client.info("memory")["used_memory"])

    source = private_falkordb.graph()
    source.query("UNWIND range(1, 20000) AS i CREATE (:N {v: i, s: 'xxxxxxxxxxxxxxxxxxxxxxxx'})")
    with_source = used()
    for k in range(COPIES):
        client.execute_command("GRAPH.COPY", source.name, f"{source.name}:sim:{k}")
    peak = used()
    for k in range(COPIES):
        client.execute_command("GRAPH.DELETE", f"{source.name}:sim:{k}")

    deadline = time.monotonic() + 15  # ASYNC_DELETE frees in the background
    while used() - with_source > 0.1 * (peak - with_source) and time.monotonic() < deadline:
        time.sleep(0.25)
    after = used()
    source.delete()

    grown_mb = (peak - with_source) / 2**20
    residue_mb = (after - with_source) / 2**20
    print(f"S13 copies grew used_memory by {grown_mb:.1f} MB; residue after deletes {residue_mb:.2f} MB")
    facts.expect("S13", grown_mb > 10 and residue_mb <= 0.1 * grown_mb)
