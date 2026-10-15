"""§15.1-§15.4, §15.10-§15.11 the Executive: base snapshots, candidate sets, the decision state machine, flushes."""

import pytest

from gnomon.executive import scoring
from gnomon.kernel.outcomes import Code
from tests.support.exec_world import build

pytestmark = [pytest.mark.integration, pytest.mark.gate_tb]

UNGATED = ["NOMINATED", "SNAPSHOT", "ENUMERATE", "BRANCH", "SIMULATE", "SCORE", "SELECT", "ENVELOPE", "COMMIT",
           "RETIRE", "DONE"]


# ── base snapshot and branches ─────────────────────────────────────────────────────────────────────────────────
def test_base_snapshot_verify(xw):
    ex = xw.executive()
    base = ex.snapshot("D-1", 1)
    assert base.key == f"{xw.k.graph.name}:sim:D-1:1:base"
    assert (base.seq, base.pt, base.lc) == (xw.k.head()["seq"], xw.k.head()["pt"], xw.k.head()["lc"])
    assert base.gapless and base.linked
    xw.db.select_graph(base.key).query("MATCH (h:Head) SET h.hash = 'forged'")
    assert ex.verify(base.key).linked is False
    xw.db.select_graph(base.key).query("MATCH (r:Rewrite {seq: 3}) DETACH DELETE r")
    assert ex.verify(base.key).gapless is False
    ex.retire([base.key])
    assert xw.sim_graphs() == []


def test_branches_share_read_seq(xw):
    ex = xw.executive()
    base = ex.snapshot("D-2", 1)
    keys = [ex.branch(base, k) for k in range(3)]
    assert keys == [f"{base.key[:-4]}{k}" for k in range(3)]
    heads = {xw.db.select_graph(k).ro_query("MATCH (h:Head) RETURN h.seq, h.hash").result_set[0][0] for k in keys}
    assert heads == {base.seq}
    ex.retire([base.key, *keys])
    assert xw.sim_graphs() == []


def test_sim_apply_never_touches_live(xw):
    ex = xw.executive()
    live_before = xw.k.head()
    base = ex.snapshot("D-3", 1)
    params = {"incident": "i1", "svc": "c"}
    for k in range(1, 101):
        result = ex.sim(base, k, "IsolateService", params)
        assert result.outcome == "scored"
    assert xw.k.head() == live_before  # Head.hash, seq, pt and lc unchanged across 100 simulations
    assert xw.k.read("MATCH (s:Service {id: 'c'}) RETURN s.mode") == [["normal"]]
    assert xw.k.read("MATCH (r:Rewrite) WHERE r.id STARTS WITH 'sim:' RETURN count(r)") == [[0]]
    ex.retire([base.key])


def test_a_simulation_changes_the_branch_only(xw):
    ex = xw.executive()
    base = ex.snapshot("D-4", 1)
    result = ex.sim(base, 1, "IsolateService", {"incident": "i1", "svc": "c"}, keep=True)
    assert result.outcome == "scored" and result.branch_seq == base.seq + 1
    assert xw.db.select_graph(result.branch).ro_query("MATCH (s:Service {id: 'c'}) RETURN s.mode").result_set == [
        ["isolated"]]
    assert xw.db.select_graph(base.key).ro_query("MATCH (s:Service {id: 'c'}) RETURN s.mode").result_set == [["normal"]]
    ex.retire([base.key])


# ── candidate sets ────────────────────────────────────────────────────────────────────────────────────────────────
def test_exhaustive_candidate_set_and_integer_scores(xw):
    res = xw.executive().simulate("i1")
    assert res.state == "SIMULATED" and res.mode == "exhaustive"
    assert [c["k"] for c in res.candidates] == [0, 1, 2, 3]
    assert [(c["rule"], c["outcome"]) for c in res.candidates] == [
        ("EscalateIncident", "scored"), ("IsolateService", "scored"), ("FailoverService", "scored"),
        ("FailoverService", "guard_pruned")]
    escalate, isolate, failover, pruned = res.candidates
    assert (escalate["benefit"], escalate["harm"], escalate["score_num"]) == (0, 0, 0)  # the "no automated action" bar
    # base B = c 10080 + b 7560 + a 5040 + f 5040; isolating c drops only the soft dependent f
    assert (isolate["benefit"], isolate["harm"], isolate["teams_new"], isolate["score_num"]) == (5040, 0, 0, 10080)
    assert failover["params"]["replica"] == "c-r1"
    assert (failover["benefit"], failover["harm"], failover["score_num"]) == (27720, 0, 55440)
    assert pruned["params"]["replica"] == "c-r2" and pruned["reason"].endswith("replica_healthy")
    assert pruned["score_num"] is None and pruned["won"] is False
    assert [c["k"] for c in res.candidates if c["won"]] == [2]
    assert all(c["params_sha"] == scoring.params_sha(c["rule"], c["params"]) for c in res.candidates)


def test_escalate_is_always_k0_and_scores_zero(xw):
    for nominations in ([], [{"rule": "IsolateService", "params": {"incident": "i1", "svc": "c"}}]):
        res = xw.executive().simulate("i1", nominations)
        assert res.candidates[0]["rule"] == "EscalateIncident" and res.candidates[0]["params"] == {"incident": "i1"}
        assert res.candidates[0]["score_num"] == 0


def test_nominated_candidate_set_when_too_many_tuples(xw):
    ex = xw.executive(k_max=2)  # three enabled tuples > K_MAX: only the nominations, capped at K_MAX - 1
    nominations = [{"rule": "IsolateService", "params": {"incident": "i1", "svc": "c"}},
                   {"rule": "FailoverService", "params": {"incident": "i1", "svc": "c", "replica": "c-r1"}}]
    res = ex.simulate("i1", nominations)
    assert res.mode == "nominated"
    assert [(c["k"], c["rule"]) for c in res.candidates] == [(0, "EscalateIncident"), (1, "IsolateService")]


def test_an_invented_nomination_is_dropped(xw):
    res = xw.executive(k_max=2).simulate("i1", [{"rule": "IsolateService", "params": {"incident": "i1", "svc": "zzz"}}])
    assert [c["rule"] for c in res.candidates] == ["EscalateIncident"]
    assert res.dropped == [{"rule": "IsolateService", "params": {"incident": "i1", "svc": "zzz"}}]


def test_r13_unhealthy_replica_guard_pruned_in_branch(xw):
    res = xw.executive().simulate("i1")
    tuples = [c for c in res.candidates if c["rule"] == "FailoverService"]
    assert [t["params"]["replica"] for t in tuples] == ["c-r1", "c-r2"]  # EN-R13 lists the unhealthy one on purpose
    assert tuples[1]["outcome"] == "guard_pruned"


def test_margin_falls_back_to_escalate(opened, db, redis_raw):
    xw = build(opened, db, redis_raw, soft=False, replicas=("down", "down"))
    res = xw.executive().decide("i1", [], "nothing helps", "r1")
    assert res.state == "DONE" and res.outcome.code == Code.APPLIED
    assert [c["won"] for c in res.candidates][0] is True and res.winner["rule"] == "EscalateIncident"
    assert xw.k.read("MATCH (i:Incident {id: 'i1'}) RETURN i.escalated, i.holds") == [[True, 1]]
    assert xw.k.read("MATCH (o:Outbox {kind: 'page'}) RETURN o.target, o.incident") == [["t1", "i1"]]


# ── decide: commit, footprint, evidence ─────────────────────────────────────────────────────────────────────────────
def test_decide_commits_the_winner_with_its_evidence(xw):
    ex = xw.executive()
    res = ex.decide("i1", [], "fail over to the healthy replica", "r1", proposer="agent:rsp-1")
    assert res.state == "DONE" and res.outcome.code == Code.APPLIED and res.trace == UNGATED
    d = res.envelope
    assert d["did"].startswith("D-") and d["attempt"] == 1 and d["aid"] == f"{d['did']}/1" and d["kind"] == "action"
    assert (d["hops"], d["weight_scale"], d["lambda_n"], d["lambda_d"], d["tau"], d["margin"]) == (
        4, 2520, 3, 2, 2520, 1260)
    assert d["scorer"] == f"{xw.library}.blastRadius" and d["proposer"] == "agent:rsp-1"
    assert xw.k.read("MATCH (c:Candidate {won: true}) RETURN c.rule, c.score_num") == [["FailoverService", 55440]]
    assert xw.k.read("MATCH (s:Service {id: 'c'}) RETURN s.mode, s.active_replica, s.health") == [
        ["failed_over", "c-r1", "ok"]]
    assert xw.k.read("MATCH (o:Outbox {kind: 'failover'}) RETURN o.target, o.to, o.state") == [["c", "c-r1", "pending"]]
    assert xw.sim_graphs() == []  # RETIRE deleted the base and every branch


def test_footprint_keys_are_in_the_view_keys_of_the_commit(xw):
    res = xw.executive().decide("i1", [], "r", "r2")
    region = {"svc:a", "svc:b", "svc:c", "svc:f"}
    view_keys = set(xw.k.read("MATCH (r:Rewrite {id: $p}) RETURN r.view_keys", {"p": res.pid})[0][0])
    footprint = set(scoring.footprint(res.base_output, "i1"))
    assert footprint <= view_keys
    assert {"OWNED_BY:svc:b:*", "DEPENDS_ON:*:svc:c", "Incident:i1:state"} <= footprint
    assert set(res.base_output["region"]) == region
    assert xw.k.read("MATCH (r:Rewrite {id: $p}) RETURN r.read_seq", {"p": res.pid}) == [[res.envelope["read_seq"]]]


def test_evidence_paths_and_cites_are_live_and_inside_d_cites(xw):
    res = xw.executive().decide("i1", [], "r", "r3")
    d = res.envelope
    rescued = [e for e in d["evidence"] if e["role"] == "rescued"]
    assert [e["path"][-1] for e in rescued] == ["svc:c", "svc:b", "svc:a"]  # top EVIDENCE_N rescued, then by uid
    assert rescued[1]["path"] == ["svc:c", "svc:b"]
    cited = {(c["s"], c["fid"]) for c in d["cites"]}
    assert {(c["s"], c["fid"]) for e in d["evidence"] for c in e["cites"]} <= cited
    assert d["cites"] == sorted(d["cites"], key=lambda c: (c["s"], c["fid"]))
    assert xw.k.read("MATCH (:Decision {id: $d})-[:JUSTIFIED_BY]->(e:Evidence)-[c:CITES]->(s:Entity) "
                     "RETURN count(c)", {"d": d["did"]})[0][0] >= len(cited)


# ── state machine ────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_gated_winner_waits_for_a_human(xw_rollback):
    xw = xw_rollback
    ex = xw.executive()
    res = ex.decide("i1", [], "roll back the bad deploy", "r1")
    assert res.winner["rule"] == "RollbackDeploy" and res.state == "AWAITING"
    assert res.trace[-4:] == ["ENVELOPE", "PRECHECK", "REQUEST", "AWAITING"]
    d = res.envelope
    assert xw.k.read("MATCH (a:Approval {aid: $a}) RETURN a.state, a.decision_sha, a.rule",
                     {"a": d["aid"]}) == [["pending", d["sha"], "RollbackDeploy"]]
    assert xw.k.read("MATCH (x:Decision) RETURN count(x)") == [[0]]
    assert ex.resume(d["did"]).state == "AWAITING"  # still pending: nothing changes
    assert len(xw.sim_graphs()) > 0  # the branches stay open for the analyst until the decision is retired
    assert xw.dw.decide(d["aid"], d["sha"], "approve").code == "APPLIED"
    done = ex.resume(d["did"])
    assert done.state == "DONE" and done.outcome.code == Code.APPLIED and done.trace[-4:] == [
        "AWAITING", "COMMIT", "RETIRE", "DONE"]
    assert xw.k.read("MATCH (a:Approval {aid: $a})-[:APPROVES]->(x:Decision) RETURN x.id", {"a": d["aid"]}) == [
        [d["did"]]]
    assert xw.k.read("MATCH (s:Service {id: 'c'}) RETURN s.health") == [["ok"]]
    assert xw.sim_graphs() == []


def test_a_veto_selects_the_next_best_under_a_new_sha(xw_rollback):
    xw = xw_rollback
    ex = xw.executive()
    first = ex.decide("i1", [], "roll back", "r1")
    d1 = first.envelope
    assert xw.dw.decide(d1["aid"], d1["sha"], "veto").code == "APPLIED"
    again = ex.resume(d1["did"])
    d2 = again.envelope
    assert again.state == "DONE" and again.outcome.code == Code.APPLIED
    assert d2["did"] == d1["did"] and d2["attempt"] == 2 and d2["aid"] == f"{d1['did']}/2" and d2["sha"] != d1["sha"]
    vetoed = [c for c in d2["candidates"] if c["outcome"] == "vetoed"]
    assert [c["rule"] for c in vetoed] == ["RollbackDeploy"] and vetoed[0]["won"] is False
    assert again.winner["rule"] == "IsolateService"  # the next best, ungated
    assert xw.k.read("MATCH ()-[a:APPROVES]->() RETURN count(a)") == [[0]]  # the veto signs nothing
    assert xw.k.read("MATCH (a:Approval {aid: $a}) RETURN a.verdict", {"a": d1["aid"]}) == [["veto"]]
    assert "SELECT" in again.trace[again.trace.index("AWAITING"):]


def test_an_expired_approval_flushes_and_asks_again(opened, db, redis_raw):
    xw = build(opened, db, redis_raw, root_cause=True, replicas=("degraded", "down"))
    ex = xw.executive(approval_ttl_ms=1_000)
    first = ex.decide("i1", [], "roll back", "r1")
    assert first.state == "AWAITING"
    xw.ir.clock += 10_000  # the approval's time passes
    second = ex.resume(first.did)
    assert second.state == "AWAITING" and second.attempt == 2 and second.envelope["aid"] == f"{first.did}/2"
    assert {"FLUSH", "SNAPSHOT"} <= set(second.trace[second.trace.index("AWAITING"):])
    assert [r[0] for r in xw.k.read("MATCH (a:Approval) RETURN a.aid ORDER BY a.aid")] == [
        f"{first.did}/1", f"{first.did}/2"]
    assert all(":1:" not in g for g in xw.sim_graphs())  # attempt 1's graphs were deleted on the flush


def test_stale_flush_resnapshots_and_commits(xw):
    fired = []

    def retract_ownership(executive, decision):
        if fired:
            return
        fired.append(decision.envelope["read_seq"])
        fid = xw.k.read("MATCH (:Service {id: 'b'})-[f:OWNED_BY]->() WHERE f.te = 9007199254740991 RETURN f.fid")[0][0]
        xw.ir.ok("RetractFact.OWNED_BY", {"s": "svc:b", "o": "team:t2", "fid": fid, "mode": "end",
                                          "vt_end": xw.ir.clock}, "ops-1")

    ex = xw.executive(before_commit=retract_ownership)
    res = ex.decide("i1", [], "r", "r1")
    assert res.state == "DONE" and res.outcome.code == Code.APPLIED
    assert res.attempt == 2 and res.stale_flushes == 1 and res.trace.count("FLUSH") == 1
    assert res.envelope["read_seq"] > fired[0]  # re-snapshotted after the retraction
    assert xw.sim_graphs() == []


def test_stale_flushes_are_bounded_then_escalate_only(xw):
    fired = []

    def touch_b(executive, decision):
        if len(fired) >= 2:
            return
        fired.append(decision.envelope["read_seq"])
        xw.ir.ok("Observe.health", {"source_id": f"hx{len(fired)}", "service": "b", "health": "ok",
                                    "observed_pt": xw.ir.clock + 1}, "ing-1")  # writes Service:b:state, in the footprint

    res = xw.executive(before_commit=touch_b, stale_retries=2).decide("i1", [], "r", "r1")
    assert res.stale_flushes == 2 and res.mode == "escalate_only" and res.attempt == 3
    assert [c["rule"] for c in res.candidates] == ["EscalateIncident"]
    assert res.state == "DONE" and res.winner["rule"] == "EscalateIncident"


def test_crash_recovery_returns_duplicate(xw):
    first = xw.executive().decide("i1", [], "r", "same-request")
    assert first.outcome.code == Code.APPLIED
    head = xw.k.head()["seq"]
    again = xw.executive().decide("i1", [], "r", "same-request")  # a fresh process re-issues the same request id
    assert again.did == first.did and again.pid == first.pid
    assert again.state == "DONE" and again.outcome.code == Code.DUPLICATE and again.outcome.seq == first.outcome.seq
    assert xw.k.head()["seq"] == head and xw.sim_graphs() == []


def test_distinct_request_ids_are_distinct_decisions(xw):
    ex = xw.executive()
    a = ex.simulate("i1", request_id="a")
    b = ex.simulate("i1", request_id="b")
    assert a.did != b.did and a.did.startswith("D-")


def test_no_branch_leaks_on_success_failure_and_simulation(xw):
    ex = xw.executive()
    ex.simulate("i1")
    assert xw.sim_graphs() == []
    ex.decide("i1", [], "r", "r9")
    assert xw.sim_graphs() == []
    res = xw.executive().decide("nope", [], "r", "r10")  # an unknown incident: nothing to enumerate
    assert res.state == "FAILED" and xw.sim_graphs() == []


def test_a_decision_never_writes_live_except_its_commit_and_approval(xw_rollback):
    xw = xw_rollback
    before = xw.k.head()["seq"]
    xw.executive().simulate("i1")
    assert xw.k.head()["seq"] == before
    res = xw.executive().decide("i1", [], "r", "r1")
    assert res.state == "AWAITING" and xw.k.head()["seq"] == before + 1  # only R16
    assert [r[0] for r in xw.k.read("MATCH (r:Rewrite) WHERE r.seq > $s RETURN r.rule", {"s": before})] == [
        "RequestApproval"]
