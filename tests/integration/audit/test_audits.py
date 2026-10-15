"""Audits K1–K6 (master §12.3, §21.1) on a clean history: every check passes, and each reports in its own shape."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g3]

ALL_CHECKS = ["K1", "K2", "K3", "K4", "K5a", "K5b", "K5c", "K5d", "K6"]


def test_k1_chain_intact(db, scenario):
    from gnomon.audit import k1

    result = k1.check(db.select_graph(scenario.key))
    assert (result.name, result.ok, result.findings) == ("K1", True, [])


def test_k1_checks_linkage_across_batches(db, scenario):
    from gnomon.audit import k1

    assert k1.check(db.select_graph(scenario.key), batch=3).ok


def test_k1_names_the_broken_link(db, scratch):
    from gnomon.audit import k1

    graph = db.select_graph(scratch)
    graph.query("MATCH (r:Rewrite {id: 'co-2'}) SET r.prev = 'e' + substring(r.prev, 1)")
    seq = graph.ro_query("MATCH (r:Rewrite {id: 'co-2'}) RETURN r.seq").result_set[0][0]
    result = k1.check(graph, batch=4)
    assert not result.ok
    assert {(f["seq"], f["problem"]) for f in result.findings} >= {(seq, "prev_mismatch"), (seq, "hash_mismatch")}


def test_k2_gapless(db, scenario):
    from gnomon.audit import k2

    result = k2.check(db.select_graph(scenario.key))
    assert (result.name, result.ok) == ("K2", True)
    assert result.findings == []


def test_k3_rules_and_kernel(db, redis_raw, scenario):
    from gnomon.audit import k3

    result = k3.check(db.select_graph(scenario.key), redis_raw)
    assert (result.name, result.ok, result.findings) == ("K3", True, [])


def test_k3_kernel_sha_reads_the_loaded_library(db, redis_raw, scratch):
    from gnomon.audit import k3

    graph = db.select_graph(scratch)
    graph.query("MATCH (h:Head) SET h.kernel_sha = 'b' + substring(h.kernel_sha, 1)")
    result = k3.check(graph, redis_raw)
    assert [f["problem"] for f in result.findings] == ["kernel_sha"]


def test_k3_flags_a_rule_used_before_registration(db, redis_raw, scratch):
    from gnomon.audit import k3

    graph = db.select_graph(scratch)
    graph.query("MATCH (ru:Rule {name: 'PostUpdate'}) SET ru.registered_seq = 999")
    problems = {f["problem"] for f in k3.check(graph, redis_raw).findings}
    assert "rule_not_active" in problems


def test_k4_replay_equals_live(db, redis_raw, scenario):
    from gnomon.audit import k4

    result = k4.check(db, redis_raw, scenario.key)
    assert (result.name, result.ok, result.findings) == ("K4", True, [])
    assert not [g for g in redis_raw.execute_command("GRAPH.LIST") if g.startswith(f"{scenario.key}:replay:")]


def test_k4_reports_the_first_differing_line(db, redis_raw, scratch):
    from gnomon.audit import k4

    db.select_graph(scratch).query("MATCH (s:Service {id: 'b'}) SET s.tier = 'forged'")
    result = k4.check(db, redis_raw, scratch)
    assert not result.ok
    [finding] = result.findings
    assert finding["problem"] == "state_differs"
    assert finding["live"].startswith("N|svc:b|") and '"tier":"forged"' in finding["live"]
    assert finding["replay"].startswith("N|svc:b|") and '"tier":"t1"' in finding["replay"]


def test_k4_reports_divergence(db, redis_raw, scratch):
    from gnomon.audit import k4

    db.select_graph(scratch).query("MATCH (r:Rewrite {id: 'co-2'}) SET r.agent = 'inv-1'")
    [finding] = k4.check(db, redis_raw, scratch).findings
    assert finding["problem"] == "divergence"
    assert finding["reason"] in ("rejected", "mismatch")


def test_k5_four_parts(db, scenario):
    from gnomon.audit import k5

    results = k5.check(db.select_graph(scenario.key))
    assert [(r.name, r.ok, r.findings) for r in results] == [
        ("K5a", True, []), ("K5b", True, []), ("K5c", True, []), ("K5d", True, [])]


def test_k5_covers_the_scenario(db, scenario):
    """The clean pass is meaningful: the scenario holds a lease, a citation, a projection and a voided version."""
    graph = db.select_graph(scenario.key)
    one = lambda q: graph.ro_query(q).result_set[0][0]  # noqa: E731
    assert one("MATCH (:Agent)-[l:HOLDS]->(:Task) WHERE l.closed_seq IS NULL RETURN count(l)") >= 1
    assert one("MATCH ()-[c:CITES]->() RETURN count(c)") >= 1
    assert one("MATCH ()-[p:DEP_NOW]->() RETURN count(p)") >= 1
    assert one("MATCH ()-[f:DEPENDS_ON]->() WHERE f.te < 9007199254740991 RETURN count(f)") >= 1


def test_k6_causality(db, scenario):
    from gnomon.audit import k6

    result = k6.check(db.select_graph(scenario.key))
    assert (result.name, result.ok, result.findings) == ("K6", True, [])


def test_audit_runner_on_a_clean_history(db, redis_raw, scenario):
    from gnomon.audit.runner import audit

    report = audit(db, redis_raw, scenario.key)
    assert list(report.results) == ALL_CHECKS
    assert report.ok and report.failed == []
    assert report.as_dict()["ok"] is True
    assert set(report.as_dict()["checks"]) == set(ALL_CHECKS)


def test_audit_runner_selects_checks(db, redis_raw, scenario):
    from gnomon.audit.runner import audit

    report = audit(db, redis_raw, scenario.key, checks=["K2", "K5"])
    assert list(report.results) == ["K2", "K5a", "K5b", "K5c", "K5d"]
    with pytest.raises(ValueError):
        audit(db, redis_raw, scenario.key, checks=["K9"])
