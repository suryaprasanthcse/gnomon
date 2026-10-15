"""§15.13 K7 in full: committed Executive decisions re-simulate from the log (steps 1-8)."""

import pytest

from gnomon.audit import k7
from gnomon.audit.runner import audit

pytestmark = [pytest.mark.integration, pytest.mark.gate_tb]


@pytest.fixture
def decided(xw):
    res = xw.executive().decide("i1", [], "fail over", "r1")
    assert res.outcome.code == "APPLIED"
    return xw, res


def problems(report):
    return [f["problem"] for f in report.results["K7"].findings]


def test_k7_full_passes_on_committed_decisions(decided, db, redis_raw):
    xw, res = decided
    report = audit(db, redis_raw, xw.k.graph.name, ["K7"])
    assert report.ok, report.results["K7"].findings
    assert xw.sim_graphs() == []  # K7 retires every graph it builds, including the rebuilt base


def test_k7_passes_for_a_gated_decision_with_a_veto_history(xw_rollback, db, redis_raw):
    xw = xw_rollback
    ex = xw.executive()
    first = ex.decide("i1", [], "roll back", "r1")
    xw.dw.decide(first.envelope["aid"], first.envelope["sha"], "veto")
    done = ex.resume(first.did)
    assert done.outcome.code == "APPLIED"
    report = audit(db, redis_raw, xw.k.graph.name, ["K7"])
    assert report.ok, report.results["K7"].findings


def test_k7_full_alongside_the_core_audits_and_replay(decided, db, redis_raw):
    xw, _ = decided
    assert audit(db, redis_raw, xw.k.graph.name, ["K1", "K2", "K3", "K4", "K5", "K6", "K7"]).ok


def test_a_tampered_candidate_is_named_by_the_node_check(decided, db, redis_raw):
    xw, res = decided
    xw.k.graph.query("MATCH (c:Candidate {won: true}) SET c.benefit = c.benefit + 1")
    report = audit(db, redis_raw, xw.k.graph.name, ["K7"])
    assert "node_mismatch" in problems(report)


def test_a_changed_scorer_library_is_named(xw_private, db, redis_raw):
    from gnomon.udf import loader

    xw = xw_private
    assert xw.executive().decide("i1", [], "fail over", "r1").outcome.code == "APPLIED"
    code = loader.listed_code(redis_raw, xw.library)
    loader.load(redis_raw, xw.library, code + "\n// a different library under the same name\n")
    report = audit(db, redis_raw, xw.k.graph.name, ["K7"])
    assert "library_sha" in problems(report)


def test_a_changed_lambda_is_a_config_and_resimulation_mismatch(decided, db, redis_raw, monkeypatch):
    from gnomon.executive import scoring

    xw, res = decided
    monkeypatch.setattr(scoring, "LAMBDA_D", 3)
    found = problems(audit(db, redis_raw, xw.k.graph.name, ["K7"]))
    assert "config_mismatch" in found and "resim_mismatch" in found


def test_a_wrong_recorded_winner_is_named(decided, db, redis_raw):
    xw, res = decided
    xw.k.graph.query("MATCH (c:Candidate {k: 1}) WHERE c.cid STARTS WITH $d SET c.won = true", {"d": res.did})
    found = problems(audit(db, redis_raw, xw.k.graph.name, ["K7"]))
    assert "node_mismatch" in found  # the node no longer equals the attach


def test_decisions_by_other_scorers_are_not_resimulated(xw, db, redis_raw):
    d = xw.dw.envelope("D-N")  # a hand-built decision by the test scorer: steps 1 and 8 only
    assert xw.dw.commit(d).code == "APPLIED"
    assert audit(db, redis_raw, xw.k.graph.name, ["K7"]).ok
