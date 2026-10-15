"""§6.10 D6: every P1 failure is REJECTED_DECISION with its sub-reason, and nothing is written."""

import dataclasses

import pytest

from gnomon.executive import envelope

pytestmark = [pytest.mark.integration, pytest.mark.gate_ta]


def refused(dw, out, reason):
    assert out.code == "REJECTED_DECISION", out
    assert out.detail["reason"] == reason, out.detail


def unchanged(dw, before, action):
    out = action()
    assert dw.k.head()["seq"] == before and dw.k.read("MATCH (x:Decision) RETURN count(x)") == [[0]]
    assert dw.k.read("MATCH (m:ActionMark) RETURN count(m)") == [[0]]
    return out


def test_scorer_inactive(dw):
    d = dw.envelope("D-1")
    dw.ir.ok("RegisterScorer", {"name": "Test.scorer", "sha": "d" * 64, "kind": "measured", "version": 2}, "ops-1")
    before = dw.k.head()["seq"]
    refused(dw, unchanged(dw, before, lambda: dw.commit(d)), "scorer_inactive")  # the pinned sha was replaced


def test_scorer_unknown(dw):
    refused(dw, dw.commit(dw.envelope("D-1", scorer_sha="e" * 64)), "scorer_inactive")


def test_approval_missing_for_a_gated_rule(dw):
    refused(dw, dw.commit(dw.envelope("D-2"), rule="NoopActionGated"), "approval_missing")


def test_approval_pending(dw):
    d = dw.envelope("D-3")
    assert dw.request(d).code == "APPLIED"
    refused(dw, dw.commit(d, rule="NoopActionGated"), "approval_pending")


def test_approval_vetoed(dw):
    d = dw.envelope("D-4")
    assert dw.request(d).code == "APPLIED" and dw.decide(d["aid"], d["sha"], "veto").code == "APPLIED"
    refused(dw, dw.commit(d, rule="NoopActionGated"), "approval_vetoed")


def test_approval_sha_mismatch(dw):
    approved = dw.approved("D-5")
    changed = envelope.seal({k: v for k, v in approved.items() if k != "sha"} | {"footprint_digest": "other"})
    assert changed["aid"] == approved["aid"] and changed["sha"] != approved["sha"]
    refused(dw, dw.commit(changed, rule="NoopActionGated"), "approval_sha_mismatch")


def test_dead_cite(dw):
    ir = dw.ir
    d = dw.envelope("D-6")
    fid = ir.owned_by_fid()
    ir.ok("RetractFact.OWNED_BY", {"s": "svc:a", "o": "team:t1", "fid": fid, "mode": "end", "vt_end": ir.clock},
          "ops-1")
    refused(dw, dw.commit(dw.envelope("D-6", read_seq=d["read_seq"] + 1)), "cite_not_live")


def test_attach_sha_mismatch(dw):
    d = dw.envelope("D-7")
    p = {**dw.params(d), "attach_sha": "0" * 64}
    refused(dw, dw.commit(d, params=p), "attach_sha_mismatch")
    other = envelope.attach_of(dw.envelope("D-7", footprint_digest="x"))  # an attach that is not the one hashed
    refused(dw, dw.commit(d, attach=other), "attach_sha_mismatch")


def test_decision_sha_mismatch(dw):
    d = dw.envelope("D-8")
    refused(dw, dw.commit(d, params={**dw.params(d), "decision_sha": "1" * 64}), "decision_sha_mismatch")


def test_read_seq_mismatch(dw):
    d = dw.envelope("D-9")
    assert d["read_seq"] >= 1
    refused(dw, dw.commit(d, read_seq=d["read_seq"] - 1), "read_seq_mismatch")


def test_a_stale_view_is_still_rejected_stale(dw):
    d = dw.envelope("D-10")
    dw.ir.ok("RegisterEntity.Team", {"id": "t8", "name": "t8"}, "ops-1")
    dw.ir.clock += 10
    out = dw.k.submit("NoopAction", dw.params(d), pid=envelope.pid_of("D-10"), agent="exe-1", read_seq=d["read_seq"],
                      view_keys=["Team:*"], attach=envelope.attach_of(d), mode="replay", rec_pt=dw.ir.clock, rec_lc=0)
    assert out.code == "REJECTED_STALE"  # D5 runs before D6


def test_the_caller_must_be_an_executive(dw):
    out = dw.commit(dw.envelope("D-11"), agent="inv-1")
    assert out.code == "CALLER_DENIED"


def test_a_guard_failure_after_a_good_decision_is_still_a_guard(dw):
    assert dw.commit(dw.envelope("D-12")).code == "APPLIED"
    out = dw.commit(dw.envelope("D-13"), params=dw.params(dw.envelope("D-13"), {"mark": "m0"}))
    assert out.code == "REJECTED_GUARD" and out.detail["guard"].endswith("mark_new")


def test_decision_rules_carry_the_flags(dw):
    rule = dw.k.registry.get("NoopActionGated")
    assert dataclasses.replace(rule).requires_decision and rule.gated and rule.cls == "action"
