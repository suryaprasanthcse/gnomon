"""§6.4 P1 and §6.7 E0: a decided rule commits with its Decision, Candidates, Evidence, CITES and COMMITTED_AS."""

import json

import pytest

from gnomon.executive import envelope

pytestmark = [pytest.mark.integration, pytest.mark.gate_ta]


def test_activation_is_logged_by_rewrites(dw):
    rules = dict(dw.k.read("MATCH (r:Rewrite) WHERE r.rule IN ['RegisterRule', 'RegisterAgent', 'RegisterScorer'] "
                           "RETURN r.id, r.rule"))
    assert rules["m3-RegisterScorer"] == "RegisterRule" and rules["m3-ApproveDecision"] == "RegisterRule"
    assert dw.k.read("MATCH (s:Scorer {name: 'Test.scorer'}) RETURN s.active, s.kind") == [[True, "measured"]]
    assert dw.k.read("MATCH (r:Rule {name: 'NoopAction'}) RETURN r.requires_decision, r.gated, r.class") == [
        [True, False, "action"]]


def test_decided_commit(dw):
    ir = dw.ir
    d = dw.envelope("D-1")
    out = dw.commit(d)
    assert out.code == "APPLIED", out
    assert ir.one("MATCH (m:ActionMark {id: 'm0'}) RETURN m.rw") == [envelope.pid_of("D-1")]
    dec = ir.one("MATCH (x:Decision {id: 'D-1'}) RETURN x.attempt, x.sha, x.kind, x.proposer, x.rationale_ref, "
                 "x.scorer, x.scorer_sha, x.read_seq, x.hops, x.lambda_n, x.lambda_d, x.tau, x.margin, "
                 "x.weight_scale, x.footprint_digest, x.rw")
    assert dec == [1, d["sha"], "action", "agent:inv-1", "llm:9a0f", "Test.scorer", "c" * 64, d["read_seq"], 4, 3, 2,
                   2520, 1260, 2520, "f0", envelope.pid_of("D-1")]
    assert ir.one("MATCH (x:Decision {id: 'D-1'})-[:COMMITTED_AS]->(r:Rewrite) RETURN r.id, r.rule, r.attach") == [
        envelope.pid_of("D-1"), "NoopAction", envelope.attach_of(d)]
    cands = ir.k.read("MATCH (:Decision {id: 'D-1'})-[:CONSIDERED]->(c:Candidate) "
                      "RETURN c.cid, c.k, c.rule, c.outcome, c.reason, c.benefit, c.harm, c.teams_new, c.score_num, c.won "
                      "ORDER BY c.k")
    assert cands == [["D-1:0", 0, "NoopAction", "guard_pruned", "G1 mark_new", None, None, None, None, False],
                     ["D-1:1", 1, "NoopAction", "scored", None, 52920, 15120, 1, 60480, True]]
    assert ir.one("MATCH (:Decision {id: 'D-1'})-[:JUSTIFIED_BY]->(e:Evidence)-[c:CITES]->(s:Entity) "
                  "RETURN e.eid, e.ord, e.algo, e.role, e.path, c.fid, s.uid") == [
        "D-1:0", 0, "Test.scorer", "rescued", ["svc:a"], ir.owned_by_fid(), "svc:a"]
    assert json.loads(ir.one("MATCH (r:Rewrite {id: $p}) RETURN r.params", p=envelope.pid_of("D-1"))[0]) == dw.params(
        d)


def test_the_decision_rewrite_is_chained_and_keyed_like_any_other(dw):
    d = dw.envelope("D-2")
    assert dw.commit(d).code == "APPLIED"
    pid = envelope.pid_of("D-2")
    assert dw.ir.write_keys(pid) == ["ActionMark:*", "ActionMark:m0"]
    assert dw.k.read("MATCH (r:Rewrite {id: $p})-[:CAUSED_BY]->(:Rewrite) RETURN count(r)", {"p": pid}) == [[0]]
    assert dw.k.head()["seq"] == dw.k.read("MATCH (r:Rewrite {id: $p}) RETURN r.seq", {"p": pid})[0][0]


def test_replaying_the_same_decision_is_a_duplicate(dw):
    d = dw.envelope("D-3")
    assert dw.commit(d).code == "APPLIED"
    assert dw.commit(d).code == "DUPLICATE"
    assert dw.k.read("MATCH (x:Decision {id: 'D-3'}) RETURN count(x)") == [[1]]


def test_gated_commit_consumes_the_approval(dw):
    ir = dw.ir
    d = dw.approved("D-4")
    out = dw.commit(d, rule="NoopActionGated")
    assert out.code == "APPLIED", out
    assert ir.one("MATCH (a:Approval {aid: 'D-4/1'})-[:APPROVES]->(x:Decision) RETURN a.state, a.verdict, x.id") == [
        "decided", "approve", "D-4"]


def test_an_ungated_commit_needs_no_approval_and_creates_no_approves_edge(dw):
    assert dw.commit(dw.envelope("D-5")).code == "APPLIED"
    assert dw.k.read("MATCH ()-[a:APPROVES]->() RETURN count(a)") == [[0]]


def test_non_decision_rules_are_untouched(dw):
    ir = dw.ir
    assert ir.ok("RegisterEntity.Team", {"id": "t7", "name": "t7"}, "ops-1")
    assert dw.k.read("MATCH (x:Decision) RETURN count(x)") == [[0]]
    out = dw.k.submit("NoopAction", {"mark": "x"}, pid="bare", agent="exe-1", mode="replay", rec_pt=ir.clock + 50,
                      rec_lc=0)  # a decision rule without an attach envelope
    assert out.code == "SCHEMA_REJECTED" and "attach" in out.detail["reason"]
