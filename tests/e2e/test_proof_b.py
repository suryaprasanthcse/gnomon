"""Proof B · Gate, veto, re-selection (master §26): humans sign exactly what they saw.

The rollback wins and is gated; the analyst vetoes; the next best commits under a new sha; the APPROVES edge appears
only on a decision whose approval was an approve.
"""

import pytest

from gnomon.audit.runner import audit

pytestmark = [pytest.mark.e2e, pytest.mark.gate_tb]


def test_rollback_wins_is_vetoed_and_the_next_best_commits(xw_rollback, db, redis_raw):
    xw = xw_rollback
    ex = xw.executive()
    first = ex.decide("i1", [], "roll back the bad deploy", "proof-b")
    assert first.winner["rule"] == "RollbackDeploy" and first.state == "AWAITING"
    d1 = first.envelope
    assert xw.k.read("MATCH (a:Approval {aid: $a}) RETURN a.state, a.decision_sha, a.summary",
                     {"a": d1["aid"]})[0][:2] == ["pending", d1["sha"]]  # the human is shown exactly this sha
    assert xw.dw.decide(d1["aid"], d1["sha"], "veto").code == "APPLIED"
    second = ex.resume(first.did)
    d2 = second.envelope
    assert second.state == "DONE" and second.winner["rule"] != "RollbackDeploy" and d2["sha"] != d1["sha"]
    assert [c["outcome"] for c in d2["candidates"] if c["rule"] == "RollbackDeploy"] == ["vetoed"]
    assert xw.k.read("MATCH ()-[a:APPROVES]->() RETURN count(a)") == [[0]]
    assert xw.k.read("MATCH (s:Service {id: 'c'}) RETURN s.mode") == [["isolated"]]  # the vetoed rollback never ran
    assert audit(db, redis_raw, xw.k.graph.name, ["K1", "K4", "K7"]).ok


def test_an_approved_rollback_commits_with_its_approves_edge(xw_rollback, db, redis_raw):
    xw = xw_rollback
    ex = xw.executive()
    first = ex.decide("i1", [], "roll back", "proof-b2")
    d = first.envelope
    assert xw.dw.decide(d["aid"], d["sha"], "approve").code == "APPLIED"
    done = ex.resume(first.did)
    assert done.state == "DONE" and done.winner["rule"] == "RollbackDeploy"
    assert xw.k.read("MATCH (a:Approval)-[:APPROVES]->(x:Decision) RETURN a.aid, a.verdict, x.id") == [
        [d["aid"], "approve", d["did"]]]
    assert xw.k.read("MATCH (s:Service {id: 'c'}) RETURN s.health, s.mode") == [["ok", "normal"]]
    assert audit(db, redis_raw, xw.k.graph.name, ["K1", "K4", "K7"]).ok


def test_an_approval_for_one_sha_does_not_unlock_another(xw_rollback):
    xw = xw_rollback
    ex = xw.executive()
    first = ex.decide("i1", [], "roll back", "proof-b3")
    d = first.envelope
    assert xw.dw.decide(d["aid"], "f" * 64, "approve").code == "REJECTED_GUARD"  # R17 refuses a sha that was not asked
    assert xw.k.read("MATCH (a:Approval {aid: $a}) RETURN a.state", {"a": d["aid"]}) == [["pending"]]
    assert ex.resume(first.did).state == "AWAITING"
