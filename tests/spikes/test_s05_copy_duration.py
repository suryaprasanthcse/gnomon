"""S5 · GRAPH.COPY duration at demo size (a 16,000-rewrite log), on a quiet private server."""

import statistics
import time

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]

DEMO_REWRITES = 16_000
BUDGET_MS = 1_000  # a decision copies the base once plus one branch per candidate


def test_s05_copy_duration_at_demo_size(private_falkordb, build_log, facts):
    source = private_falkordb.graph()
    build_log(source, DEMO_REWRITES)
    client = private_falkordb.client
    timings_ms = []
    try:
        for k in range(3):
            started = time.perf_counter()
            client.execute_command("GRAPH.COPY", source.name, f"{source.name}:sim:{k}")
            timings_ms.append((time.perf_counter() - started) * 1000)
        copy = private_falkordb.graph(f"{source.name}:sim:0")
        assert copy.query("MATCH (r:Rewrite) RETURN count(r)").result_set == [[DEMO_REWRITES]]
        assert copy.query("MATCH ()-[c:CAUSED_BY]->() RETURN count(c)").result_set == [[DEMO_REWRITES - 1]]
    finally:
        for name in client.execute_command("GRAPH.LIST"):
            if name.startswith(source.name):
                client.execute_command("GRAPH.DELETE", name)

    print(f"S5 copy ms at {DEMO_REWRITES} rewrites: {[round(t, 1) for t in timings_ms]}")
    facts.expect("S5", statistics.median(timings_ms) < BUDGET_MS)
