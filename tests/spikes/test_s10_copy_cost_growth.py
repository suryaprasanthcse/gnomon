"""S10 · Copy cost as the log grows (private server)."""

import time

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]

SIZES = [2_000, 8_000, 32_000]


def test_s10_copy_cost_grows_linearly_with_the_log(private_falkordb, build_log, facts):
    client = private_falkordb.client
    best_ms = {}
    for size in SIZES:
        source = private_falkordb.graph()
        build_log(source, size)
        runs = []
        for k in range(3):
            started = time.perf_counter()
            client.execute_command("GRAPH.COPY", source.name, f"{source.name}:sim:{k}")
            runs.append((time.perf_counter() - started) * 1000)
        best_ms[size] = min(runs)
        for name in client.execute_command("GRAPH.LIST"):
            if name.startswith(source.name):
                client.execute_command("GRAPH.DELETE", name)

    print(f"S10 best copy ms by log size: { {k: round(v, 1) for k, v in best_ms.items()} }")
    growth = best_ms[SIZES[-1]] / max(best_ms[SIZES[0]], 0.001)
    size_ratio = SIZES[-1] / SIZES[0]
    # Linear or better (2x slack for noise); quadratic growth would be ~256x here.
    facts.expect("S10", growth <= 2 * size_ratio and best_ms[SIZES[-1]] < 2_000)
