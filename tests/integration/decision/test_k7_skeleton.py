"""K7 skeleton (master §15.13 steps 1 and 8): envelope sha, node-versus-attach equality, and I11."""

import pytest

from gnomon.audit import k7
from gnomon.audit.runner import audit

pytestmark = [pytest.mark.integration, pytest.mark.gate_ta]


@pytest.fixture
def decided(dw):
    assert dw.commit(dw.envelope("D-1")).code == "APPLIED"
    d2 = dw.approved("D-2")
    assert dw.commit(d2, rule="NoopActionGated", params=dw.params(d2, {"mark": "m2"})).code == "APPLIED"
    return dw


def test_clean_history_passes(decided):
    result = k7.check(decided.k.graph)
    assert (result.name, result.ok, result.findings) == ("K7", True, [])


def test_a_graph_without_decisions_passes(opened):
    assert k7.check(opened.k.graph).ok


def test_k7_is_selectable_in_the_runner_and_replay_still_holds(decided, db, redis_raw):
    name = decided.k.graph.name
    report = audit(db, redis_raw, name, ["K7"])
    assert report.ok and list(report.results) == ["K7"]
    full = audit(db, redis_raw, name)
    assert full.ok and "K7" not in full.results  # K1-K6 stay the default set until the layer is active
    assert audit(db, redis_raw, name, ["K1", "K4", "K7"]).ok  # K4 replays the decided rewrites too


def test_a_recomputed_sha_that_disagrees_is_named(decided):
    decided.k.graph.query("MATCH (r:Rewrite {rule: 'NoopAction'}) SET r.attach = replace(r.attach, 'f0', 'f1')")
    problems = [f["problem"] for f in k7.check(decided.k.graph).findings]
    assert "sha_mismatch" in problems


def test_a_node_that_differs_from_the_attach_is_named(decided):
    decided.k.graph.query("MATCH (c:Candidate {cid: 'D-1:1'}) SET c.score_num = 1")
    (finding,) = k7.check(decided.k.graph).findings
    assert (finding["problem"], finding["node"], finding["id"]) == ("node_mismatch", "Candidate", "D-1:1")
    decided.k.graph.query("MATCH (c:Candidate {cid: 'D-1:1'}) SET c.score_num = 60480")
    decided.k.graph.query("MATCH (x:Decision {id: 'D-1'}) SET x.hops = 9")
    (finding,) = k7.check(decided.k.graph).findings
    assert (finding["problem"], finding["node"], finding["id"]) == ("node_mismatch", "Decision", "D-1")


def test_an_evidence_node_that_differs_is_named(decided):
    decided.k.graph.query("MATCH (e:Evidence {eid: 'D-1:0'}) SET e.role = 'owners'")
    (finding,) = k7.check(decided.k.graph).findings
    assert (finding["problem"], finding["node"]) == ("node_mismatch", "Evidence")


def test_i11_an_action_rewrite_without_a_decision(decided):
    decided.k.graph.query("MATCH (x:Decision {id: 'D-1'}) DETACH DELETE x")
    problems = [(f["problem"], f.get("rewrite")) for f in k7.check(decided.k.graph).findings]
    assert ("i11_no_decision", "p-" + __import__("hashlib").sha256(b"D-1").hexdigest()[:24]) in problems


def test_i11_a_decision_without_a_committing_rewrite(decided):
    decided.k.graph.query("MATCH (:Decision {id: 'D-1'})-[c:COMMITTED_AS]->() DELETE c")
    problems = [f["problem"] for f in k7.check(decided.k.graph).findings]
    assert "i11_orphan_decision" in problems and "i11_no_decision" in problems


def test_i11_two_decisions_for_one_rewrite(decided):
    decided.k.graph.query("MATCH (r:Rewrite {rule: 'NoopAction'}) CREATE (:Decision {id: 'forged', sha: 'x'})-"
                          "[:COMMITTED_AS]->(r)")
    assert "i11_many_decisions" in [f["problem"] for f in k7.check(decided.k.graph).findings]
