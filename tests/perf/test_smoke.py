"""A16 · gate ASSURE · performance smoke (master §22, RUNBOOK_A A16): numbers are recorded, never thresholded.

Three measurements on the three-tenant assurance world, written to the junit properties and printed: the p50 and p95
latency of applying a proposal, the time of GRAPH.COPY at the demo graph size (the cost of a base snapshot or a branch),
and the latency of one Executive decision. The only assertions are that every measured call did what it was asked to, so
a number is never taken from a failing path. Read the numbers as one machine on one container, not as a benchmark.
"""

import statistics
import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_assure]

APPLY_SAMPLES = 60
COPY_SAMPLES = 5
DECISIONS = 3


def quantile(samples, q):
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def record(request, **numbers):
    for name, value in numbers.items():
        request.node.user_properties.append((name, round(value, 3)))
    print("PERF", {k: round(v, 3) for k, v in numbers.items()})


def test_apply_latency_p50_p95(request, aw):
    ir = aw.sync("A")
    samples = []
    for i in range(APPLY_SAMPLES):
        ir.clock = max(ir.clock, aw.k["A"].head()["pt"]) + 10
        started = time.perf_counter()
        out = ir.submit("Observe.health", {"source_id": f"perf-{i}", "service": "b", "health": "ok" if i % 2 else "degraded",
                                           "observed_pt": ir.clock}, "ing-1")
        samples.append((time.perf_counter() - started) * 1000)
        assert out.code == "APPLIED", out
    record(request, apply_p50_ms=statistics.median(samples), apply_p95_ms=quantile(samples, 0.95),
           apply_max_ms=max(samples), apply_samples=len(samples))
    assert len(samples) == APPLY_SAMPLES


def test_copy_time_at_demo_size(request, aw):
    key = aw.keys["A"]
    graph = aw.db.select_graph(key)
    nodes = graph.ro_query("MATCH (n) RETURN count(n)").result_set[0][0]
    edges = graph.ro_query("MATCH ()-[e]->() RETURN count(e)").result_set[0][0]
    samples = []
    for i in range(COPY_SAMPLES):
        target = f"{key}:perf-copy-{i}"
        started = time.perf_counter()
        aw.client.execute_command("GRAPH.COPY", key, target)
        samples.append((time.perf_counter() - started) * 1000)
        assert aw.db.select_graph(target).ro_query("MATCH (n) RETURN count(n)").result_set[0][0] == nodes
        aw.client.execute_command("GRAPH.DELETE", target)
    record(request, copy_median_ms=statistics.median(samples), copy_max_ms=max(samples), graph_nodes=nodes,
           graph_edges=edges)


def test_decision_latency(request, aw):
    samples = []
    for i in range(DECISIONS):
        executive = aw.executive("A")
        started = time.perf_counter()
        res = executive.decide("i1", [], "perf", f"perf-{i}")
        samples.append((time.perf_counter() - started) * 1000)
        assert res.state in ("DONE", "AWAITING"), (res.state, res.error)
    simulated = []
    for i in range(DECISIONS):
        started = time.perf_counter()
        res = aw.executive("B").simulate("i1", [], "perf", f"sim-{i}")
        simulated.append((time.perf_counter() - started) * 1000)
        assert res.state == "SIMULATED"
    record(request, decide_median_ms=statistics.median(samples), decide_max_ms=max(samples),
           simulate_median_ms=statistics.median(simulated), candidates=len(res.candidates))
