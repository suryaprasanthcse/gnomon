"""§15.5-§15.6 Exec.blastRadius: hand-computed impacted sets, the propagate table, the hop bound, soft bulkheads."""

import pytest

from tests.support import blast
from tests.support.exec_world import exec_library

pytestmark = [pytest.mark.integration, pytest.mark.gate_tb]


@pytest.fixture(scope="module")
def library(redis_raw):
    return exec_library(redis_raw)


@pytest.fixture
def graph(db, tenant):
    return db.select_graph(tenant)


def shape(out: dict) -> dict:
    return {"region": out["region"], "sources": out["sources"], "impacted": out["impacted"], "teams": out["teams"],
            "owner_edges": out["owner_edges"]}


@pytest.mark.parametrize("name", ["blast_chain", "blast_soft", "blast_diamond"])
def test_blast_radius_hand_computed(graph, library, name):
    spec = blast.load_spec(name)
    blast.build(graph, spec)
    out = blast.blast(graph, library, spec["seed"], spec["hops"])
    assert out["version"] == 1
    assert shape(out) == spec["expected"]


def graph_of(graph, library, health, mode, hard, crit_a=2):
    spec = {"services": {"a": {"crit": crit_a, "health": "ok", "mode": "normal"},
                         "b": {"crit": 1, "health": health, "mode": mode}},
            "edges": [["a", "b", hard, "f-ab"]], "owners": {"a": "t1", "b": "t1"}}
    blast.build(graph, spec)
    return blast.blast(graph, library, "a", 4)


# §15.6 propagate(): (state of b, mode of b, edge hard) -> (state of a, soft). crit(a) = 2, level 1 weighs 1260.
PROPAGATE = [
    ("down", "normal", True, ("down", 5040)),        # down + hard  -> down
    ("down", "normal", False, ("degraded", 2520)),   # down + soft  -> degraded, soft
    ("degraded", "normal", True, ("degraded", 2520)),  # degraded + hard -> degraded
    ("degraded", "normal", False, ("degraded", 2520)),  # degraded + soft -> degraded, soft
    ("ok", "isolated", True, ("down", 5040)),        # isolated + hard -> down (isolated reads as down)
    ("ok", "isolated", False, None),                  # isolated + soft -> nothing
]


@pytest.mark.parametrize(("health", "mode", "hard", "expected"), PROPAGATE)
def test_propagate_table(graph, library, health, mode, hard, expected):
    out = graph_of(graph, library, health, mode, hard)
    impacted = {row[0]: row for row in out["impacted"]}
    assert "svc:b" in impacted  # b is a source in every row
    if expected is None:
        assert "svc:a" not in impacted
    else:
        sev, weight = expected
        assert (impacted["svc:a"][1], impacted["svc:a"][2], impacted["svc:a"][3], impacted["svc:a"][4]) == (
            sev, 1, weight, "svc:b")


def test_a_healthy_dependency_impacts_nothing(graph, library):
    out = graph_of(graph, library, "ok", "normal", True)
    assert out["sources"] == [] and out["impacted"] == [] and out["teams"] == []


def test_region_bounded_by_hops(graph, library):
    services = {f"s{i}": {"crit": 1, "health": "down" if i == 7 else "ok", "mode": "normal"} for i in range(8)}
    edges = [[f"s{i}", f"s{i + 1}", True, f"f{i}"] for i in range(7)]
    blast.build(graph, {"services": services, "edges": edges, "owners": {}})
    near = blast.blast(graph, library, "s0", 3)
    assert near["region"] == ["svc:s0", "svc:s1", "svc:s2", "svc:s3"] and near["impacted"] == []
    far = blast.blast(graph, library, "s0", 7)
    assert far["region"] == [f"svc:s{i}" for i in range(8)] and far["sources"] == ["svc:s7"]
    reached = {row[0]: row[2] for row in far["impacted"]}
    assert reached["svc:s0"] == 7 and reached["svc:s6"] == 1


def test_the_propagation_is_bounded_by_the_hop_count_too(graph, library):
    services = {f"s{i}": {"crit": 1, "health": "down" if i == 3 else "ok", "mode": "normal"} for i in range(6)}
    edges = [[f"s{i}", f"s{i + 1}", True, f"f{i}"] for i in range(5)]
    blast.build(graph, {"services": services, "edges": edges, "owners": {}})
    out = blast.blast(graph, library, "s3", 2)
    assert [row[0] for row in out["impacted"]] == ["svc:s1", "svc:s2", "svc:s3"]  # s0 is three levels up


def test_soft_reached_nodes_do_not_propagate(graph, library):
    spec = blast.load_spec("blast_soft")
    blast.build(graph, spec)
    out = blast.blast(graph, library, "d", 4)
    assert "svc:d" not in {row[0] for row in out["impacted"]}  # d depends hard on a, but a is only soft-reached
    assert {row[0] for row in out["impacted"]} == {"svc:a", "svc:b"}


@pytest.mark.parametrize("hops", [0, 9, -1])
def test_hops_must_be_between_1_and_8(graph, library, hops):
    blast.build(graph, blast.load_spec("blast_chain"))
    with pytest.raises(Exception, match="hops"):
        blast.blast(graph, library, "a", hops)


def test_the_scorer_only_reads(graph, library):
    blast.build(graph, blast.load_spec("blast_chain"))
    before = graph.ro_query("MATCH (n) RETURN count(n)").result_set
    blast.blast(graph, library, "a", 4)
    assert graph.ro_query("MATCH (n) RETURN count(n)").result_set == before
    assert graph.ro_query("MATCH (s:Service {uid: 'svc:c'}) RETURN s.health").result_set == [["down"]]
