"""§14.4-§14.6 RequestApproval (R16), ApproveDecision (R17) and the approval state machine."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_ta]

APPROVAL = ("MATCH (a:Approval {aid: $aid}) RETURN a.state, a.verdict, a.approver, a.decision_sha, a.rule, "
            "a.params_sha, a.summary, a.did")


def approval(dw, aid):
    return dw.ir.one(APPROVAL, aid=aid)


def guard(out):
    assert out.code == "REJECTED_GUARD", out
    return out.detail["guard"].split(":", 1)[1]


def test_request_creates_a_pending_approval_for_the_incident(dw):
    d = dw.envelope("D-1")
    out = dw.request(d)
    assert out.code == "APPLIED", out
    assert approval(dw, d["aid"]) == ["pending", None, None, d["sha"], "NoopActionGated", approval(dw, d["aid"])[5],
                                      "roll back the deploy", "D-1"]
    assert dw.ir.one("MATCH (:Approval {aid: $a})-[:FOR]->(i:Incident) RETURN i.id", a=d["aid"]) == ["i1"]
    assert dw.ir.one("MATCH (a:Approval {aid: $a}) RETURN a.requested_seq, a.expires_pt", a=d["aid"])[1] == \
        dw.ir.clock + 60_000
    assert dw.ir.write_keys("req-D-1/1") == ["Approval:D-1/1", "Approval:*"]


def test_request_guards(dw):
    d = dw.envelope("D-2")
    assert dw.request(d).code == "APPLIED"
    assert dw.request(d).code == "DUPLICATE"  # the same pid is idempotent
    assert guard(dw.request(d, pid="req-again")) == "aid_new"
    other = dw.envelope("D-3")
    assert guard(dw.request(other, ttl=0)) == "ttl_positive"
    assert guard(dw.request(other, ttl=900_001)) == "ttl_max"
    assert dw.request(other, ttl=900_000).code == "APPLIED"  # the bound is inclusive
    assert guard(dw.request(dw.envelope("D-4"), incident="nope")) == "incident_exists"
    dw.ir.poke("MATCH (i:Incident {id: 'i1'}) SET i.state = 'resolved'")
    assert guard(dw.request(dw.envelope("D-5"))) == "incident_open"


def test_only_the_executive_requests_and_only_a_human_decides(dw):
    d = dw.envelope("D-6")
    assert dw.request(d, agent="inv-1").code == "CALLER_DENIED"
    assert dw.request(d).code == "APPLIED"
    assert dw.decide(d["aid"], d["sha"], agent="inv-1").code == "CALLER_DENIED"
    assert dw.decide(d["aid"], d["sha"], agent="exe-1").code == "CALLER_DENIED"


@pytest.mark.parametrize("verdict", ["approve", "veto"])
def test_a_human_decides_once(dw, verdict):
    d = dw.envelope("D-7")
    dw.request(d)
    out = dw.decide(d["aid"], d["sha"], verdict)
    assert out.code == "APPLIED", out
    state, got, approver, *_ = approval(dw, d["aid"])
    assert (state, got, approver) == ("decided", verdict, "hum-1")
    assert dw.ir.one("MATCH (a:Approval {aid: $a}) RETURN a.note, a.decided_seq", a=d["aid"]) == ["seen", out.seq]
    assert guard(dw.decide(d["aid"], d["sha"], "veto" if verdict == "approve" else "approve")) == "pending"


def test_a_signature_binds_to_the_sha_it_names(dw):
    d = dw.envelope("D-8")
    dw.request(d)
    assert guard(dw.decide(d["aid"], "f" * 64)) == "sha_matches"
    assert guard(dw.decide(d["aid"], d["sha"], "maybe")) == "verdict_valid"
    assert guard(dw.decide("D-8/9", d["sha"])) == "approval_exists"
    assert approval(dw, d["aid"])[0] == "pending"


def test_a_pending_approval_expires_and_r17_refuses(dw):
    d = dw.envelope("D-9")
    dw.request(d, ttl=1_000)
    expires = dw.ir.one("MATCH (a:Approval {aid: $a}) RETURN a.expires_pt", a=d["aid"])[0]
    assert guard(dw.decide(d["aid"], d["sha"], at=expires)) == "unexpired"  # now >= expires_pt
    assert approval(dw, d["aid"])[0] == "pending"  # expiry is derived, nothing is written
    assert dw.decide(d["aid"], d["sha"], at=expires + 1_000).code == "REJECTED_GUARD"
    retry = dw.envelope("D-9", attempt=2)  # the Executive re-requests with attempt + 1
    assert retry["aid"] == "D-9/2" and dw.request(retry).code == "APPLIED"
    assert dw.decide(retry["aid"], retry["sha"]).code == "APPLIED"


def test_a_decision_just_before_expiry_is_accepted(dw):
    d = dw.envelope("D-10")
    dw.request(d, ttl=1_000)
    expires = dw.ir.one("MATCH (a:Approval {aid: $a}) RETURN a.expires_pt", a=d["aid"])[0]
    assert dw.decide(d["aid"], d["sha"], at=expires - 1).code == "APPLIED"


def test_approval_is_keyed_by_aid_not_did(dw):
    first = dw.envelope("D-11", attempt=1)
    second = dw.envelope("D-11", attempt=2, footprint_digest="again")
    dw.request(first)
    dw.decide(first["aid"], first["sha"], "veto")
    assert dw.request(second).code == "APPLIED"  # same did, new aid: a fresh pending approval
    assert [r[0] for r in dw.k.read("MATCH (a:Approval {did: 'D-11'}) RETURN a.aid ORDER BY a.aid")] == [
        "D-11/1", "D-11/2"]
    assert approval(dw, "D-11/1")[:2] == ["decided", "veto"] and approval(dw, "D-11/2")[0] == "pending"


def test_only_a_decided_approve_unlocks_the_commit_and_it_is_consumed_once(dw):
    d = dw.approved("D-12")
    assert dw.commit(d, rule="NoopActionGated").code == "APPLIED"
    assert dw.k.read("MATCH (a:Approval {aid: 'D-12/1'})-[r:APPROVES]->(:Decision {id: 'D-12'}) RETURN count(r)") == [
        [1]]
    again = dw.commit(d, rule="NoopActionGated")
    assert again.code == "DUPLICATE"  # the same pid: the commit is idempotent
    assert dw.k.read("MATCH ()-[r:APPROVES]->() RETURN count(r)") == [[1]]


def test_scorer_registration_replaces_the_active_version(dw):
    dw.ir.ok("RegisterScorer", {"name": "Test.scorer", "sha": "d" * 64, "kind": "udf", "version": 2}, "ops-1")
    rows = dw.k.read("MATCH (s:Scorer {name: 'Test.scorer'}) RETURN s.version, s.active, s.deactivated_seq IS NOT NULL "
                     "ORDER BY s.version")
    assert rows == [[1, False, True], [2, True, False]]
    out = dw.ir.submit("RegisterScorer", {"name": "Other", "sha": "d" * 64, "kind": "udf", "version": 1}, "ops-1")
    assert guard(out) == "sha_new"
    out = dw.ir.submit("RegisterScorer", {"name": "Other", "sha": "9" * 64, "kind": "bogus", "version": 1}, "ops-1")
    assert guard(out) == "kind_valid"
    assert dw.ir.submit("RegisterScorer", {"name": "Other", "sha": "9" * 64, "kind": "udf", "version": 1},
                        "inv-1").code == "CALLER_DENIED"
