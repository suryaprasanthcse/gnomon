"""§10.5 algorithm tools over DEP_NOW on a fixture topology a→b→c→d, e→c, f isolated (signatures per FACTS F6/S17)."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4a]


def test_neighborhood_downstream_and_upstream(topology, qs):
    near = qs.neighborhood("a", 2)
    assert sorted(near["downstream"]["nodes"]) == ["svc:b", "svc:c"]
    assert near["upstream"]["nodes"] == []
    assert len(near["downstream"]["fids"]) == 2
    up = qs.neighborhood("c", 2)  # D-5: algo.bfs is outgoing-only; upstream uses a variable-length pattern
    assert sorted(up["upstream"]["nodes"]) == ["svc:a", "svc:b", "svc:e"]
    assert sorted(up["downstream"]["nodes"]) == ["svc:d"]
    assert qs.neighborhood("f", 3) == {"downstream": {"nodes": [], "fids": []}, "upstream": {"nodes": [], "fids": []}}


def test_rank_critical_is_pagerank_along_dependencies(topology, qs):
    top = qs.rank_critical(k=2)
    assert {r["service"] for r in top} == {"c", "d"}
    assert [r["score"] for r in top] == sorted((r["score"] for r in top), reverse=True)
    assert len(qs.rank_critical(k=10)) == 6


def test_brokers_use_a_fixed_seed(topology, qs):
    first = qs.brokers(k=2, sampling=6)
    assert {r["service"] for r in first} == {"b", "c"}
    assert first == qs.brokers(k=2, sampling=6)


def test_failure_domains_are_weak_components(topology, qs):
    domains = qs.failure_domains()
    assert sorted(sorted(d["services"]) for d in domains) == [["a", "b", "c", "d", "e"], ["f"]]
    assert all("component" in d for d in domains)


def test_communities_partition_the_services(topology, qs):
    seen = [s for c in qs.communities() for s in c["services"]]
    assert sorted(seen) == ["a", "b", "c", "d", "e", "f"]
    assert any(c["services"] == ["f"] for c in qs.communities())


def test_dependency_path_in_either_direction(topology, qs):
    fwd = qs.dependency_path("a", "d", 5)
    assert fwd["nodes"] == ["svc:a", "svc:b", "svc:c", "svc:d"] and len(fwd["fids"]) == 3
    back = qs.dependency_path("d", "a", 5)
    assert back["nodes"] == ["svc:d", "svc:c", "svc:b", "svc:a"]
    assert qs.dependency_path("a", "d", 2) is None  # no path within two hops
    assert qs.dependency_path("a", "f", 9) is None


def test_algorithm_tools_only_read(topology, qs):
    graph = topology.k.graph
    writes = []
    orig = graph.query
    graph.query = lambda q, p=None, *a, **kw: (writes.append(q), orig(q, p, *a, **kw))[1]
    seq = topology.k.head()["seq"]
    qs.neighborhood("a", 3), qs.rank_critical(3), qs.brokers(3, 6), qs.failure_domains(), qs.communities()
    qs.dependency_path("a", "d", 4)
    assert writes == [] and topology.k.head()["seq"] == seq
