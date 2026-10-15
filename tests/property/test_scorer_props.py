"""§15.6 determinism: two runs on equal graphs give byte-identical output, whatever the graph."""

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.conftest import tenant_scope
from tests.support import blast
from tests.support.exec_world import exec_library

pytestmark = [pytest.mark.property, pytest.mark.gate_tb]

NODES = [f"s{i}" for i in range(7)]


@st.composite
def graphs(draw):
    n = draw(st.integers(2, 7))
    ids = NODES[:n]
    services = {i: {"crit": draw(st.integers(1, 5)), "health": draw(st.sampled_from(["ok", "ok", "degraded", "down"])),
                    "mode": draw(st.sampled_from(["normal", "normal", "normal", "isolated"]))} for i in ids}
    pairs = draw(st.lists(st.tuples(st.sampled_from(ids), st.sampled_from(ids), st.booleans()), max_size=12))
    edges, seen = [], set()
    for a, b, hard in pairs:
        if a != b and (a, b) not in seen:
            seen.add((a, b))
            edges.append([a, b, hard, f"f-{a}-{b}"])
    owners = {i: draw(st.sampled_from(["t1", "t2", "t3"])) for i in ids if draw(st.booleans())}
    return {"services": services, "edges": edges, "owners": owners}, draw(st.sampled_from(ids)), draw(st.integers(1, 8))


@pytest.fixture(scope="module")
def library(redis_raw):
    return exec_library(redis_raw)


@given(case=graphs())
@settings(max_examples=25, deadline=None, suppress_health_check=list(HealthCheck))
def test_scorer_byte_identical(db, redis_raw, library, case):
    spec, seed, hops = case
    seed = seed if seed in spec["services"] else next(iter(spec["services"]))
    with tenant_scope(redis_raw) as key:
        graph = db.select_graph(key)
        blast.build(graph, spec)
        first = json.dumps(blast.blast(graph, library, seed, hops))
        second = json.dumps(blast.blast(graph, library, seed, hops))
        redis_raw.execute_command("GRAPH.COPY", key, f"{key}:copy")
        third = json.dumps(blast.blast(db.select_graph(f"{key}:copy"), library, seed, hops))
        assert first == second == third
        out = json.loads(first)
        impacted = out["impacted"]
        assert {row[0] for row in impacted} <= set(out["region"]) and out["sources"] == sorted(out["sources"])
        assert [row[0] for row in impacted] == sorted(row[0] for row in impacted)
        assert all(isinstance(row[3], int) and row[3] > 0 and 0 <= row[2] <= hops for row in impacted)
        assert all(row[4] is None or row[4] in {r[0] for r in impacted} for row in impacted)
