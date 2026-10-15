"""§15.12 action rules R12, R13, R14 (gated), R15, R18: happy paths, every guard probe, write keys."""

import pytest

from gnomon.executive import envelope

pytestmark = [pytest.mark.integration, pytest.mark.gate_tb]


def act(xw, rule, params, did, *, base=None):
    d = xw.dw.envelope(did)
    return d, xw.dw.commit(d, rule=rule, params=xw.dw.params(d, {"incident": "i1", **params}))


def guard(out):
    assert out.code == "REJECTED_GUARD", out
    return out.detail["guard"].split(":", 1)[1]


def keys(xw, d):
    return xw.ir.write_keys(envelope.pid_of(d["did"]))


# ── R12 IsolateService ──────────────────────────────────────────────────────────────────────────────────────────
def test_r12_isolates_and_queues_the_action(xw):
    d, out = act(xw, "IsolateService", {"svc": "c"}, "D-1")
    assert out.code == "APPLIED", out
    assert xw.k.read("MATCH (s:Service {id: 'c'}) RETURN s.mode, s.health, s.health_basis, s.mode_seq") == [
        ["isolated", "down", "isolated", out.seq]]
    assert xw.k.read("MATCH (o:Outbox {pid: $p}) RETURN o.kind, o.target, o.state, o.attempts",
                     {"p": envelope.pid_of("D-1")}) == [["isolate", "c", "pending", 0]]
    assert keys(xw, d) == ["Service:c:state", f"Outbox:{envelope.pid_of('D-1')}"]


@pytest.mark.parametrize(("setup", "params", "expected"), [
    ("MATCH (i:Incident {id: 'i1'}) SET i.state = 'resolved'", {"svc": "c"}, "incident_open"),
    ("MATCH (s:Service {id: 'c'}) SET s.mode = 'isolated'", {"svc": "c"}, "normal"),
    (None, {"svc": "b"}, "unhealthy"),
    ("CREATE (:Service:Entity {uid: 'svc:z', id: 'z', health: 'down', mode: 'normal', criticality: 1})",
     {"svc": "z"}, "in_region"),
], ids=["incident-closed", "already-isolated", "healthy", "outside-the-region"])
def test_r12_guards(xw, setup, params, expected):
    if setup:
        xw.ir.poke(setup)
    d, out = act(xw, "IsolateService", params, "D-1")
    assert guard(out) == expected
    assert xw.k.read("MATCH (o:Outbox {kind: 'isolate'}) RETURN count(o)") == [[0]]


# ── R13 FailoverService ───────────────────────────────────────────────────────────────────────────────────────
def test_r13_fails_over_to_a_healthy_replica(xw):
    d, out = act(xw, "FailoverService", {"svc": "c", "replica": "c-r1"}, "D-1")
    assert out.code == "APPLIED", out
    assert xw.k.read("MATCH (s:Service {id: 'c'}) RETURN s.mode, s.active_replica, s.health, s.health_basis") == [
        ["failed_over", "c-r1", "ok", "failover"]]
    assert xw.k.read("MATCH (o:Outbox {pid: $p}) RETURN o.kind, o.target, o.to", {"p": envelope.pid_of("D-1")}) == [
        ["failover", "c", "c-r1"]]
    assert keys(xw, d) == ["Service:c:state", f"Outbox:{envelope.pid_of('D-1')}"]


@pytest.mark.parametrize(("params", "expected"), [
    ({"svc": "c", "replica": "c-r2"}, "replica_healthy"),
    ({"svc": "b", "replica": "c-r1"}, "unhealthy"),
    ({"svc": "c", "replica": "f"}, "replica_of"),
], ids=["unhealthy-replica", "service-is-healthy", "not-a-replica"])
def test_r13_guards(xw, params, expected):
    assert guard(act(xw, "FailoverService", params, "D-1")[1]) == expected


# ── R14 RollbackDeploy (gated) ─────────────────────────────────────────────────────────────────────────────────
ROLLBACK = {"svc": "c", "bad": "c-bad", "prev": "c-prev"}


def test_r14_is_gated_and_rolls_back_once_approved(xw_rollback):
    xw = xw_rollback
    ungated = xw.dw.envelope("D-0")
    refused = xw.dw.commit(ungated, rule="RollbackDeploy",
                           params=xw.dw.params(ungated, {"incident": "i1", **ROLLBACK}))
    assert refused.code == "REJECTED_DECISION" and refused.detail["reason"] == "approval_missing"
    d = xw.dw.approved("D-1")
    out = xw.dw.commit(d, rule="RollbackDeploy", params=xw.dw.params(d, {"incident": "i1", **ROLLBACK}))
    assert out.code == "APPLIED", out
    pid = envelope.pid_of("D-1")
    assert xw.k.read("MATCH (s:Service {id: 'c'})-[f:DEPLOYED]->(x:Deploy) WHERE f.te = 9007199254740991 AND f.vt = 9007199254740991 "
                     "RETURN x.id") == [["c-prev"]]
    assert xw.k.read("MATCH (:Service {id: 'c'})-[f:DEPLOYED {fid: $f}]->(x:Deploy) RETURN x.id, f.prev_fid IS NOT NULL",
                     {"f": f"{pid}#e"}) == [["c-bad", True]]
    assert xw.k.read("MATCH (s:Service {id: 'c'}) RETURN s.health, s.health_basis") == [["ok", "rollback"]]
    assert xw.k.read("MATCH (o:Outbox {pid: $p}) RETURN o.kind, o.target, o.to", {"p": pid}) == [
        ["rollback", "c", "c-prev"]]
    assert xw.ir.write_keys(pid) == ["DEPLOYED:svc:c:*", "DEPLOYED:*:dep:c-bad", "DEPLOYED:*:dep:c-prev", "DEPLOYED:*",
                                     "Service:c:state", f"Outbox:{pid}"]


@pytest.mark.parametrize(("setup", "params", "expected"), [
    ("MATCH (i:Incident {id: 'i1'}) SET i.state = 'resolved'", ROLLBACK, "incident_open"),
    (None, {**ROLLBACK, "bad": "c-prev"}, "cause_exists"),
    ("MATCH (s:Service {id: 'c'}) SET s.health = 'ok'", ROLLBACK, "unhealthy"),
    ("MATCH (d:Deploy {id: 'c-prev'}) SET d.verified = false", ROLLBACK, "prev_verified"),
], ids=["incident-closed", "not-the-root-cause", "service-healthy", "previous-unverified"])
def test_r14_guards(xw_rollback, setup, params, expected):
    xw = xw_rollback
    d = xw.dw.approved("D-1")  # the approval is asked for while the incident is still open
    if setup:
        xw.ir.poke(setup)
    out = xw.dw.commit(d, rule="RollbackDeploy", params=xw.dw.params(d, {"incident": "i1", **params}))
    assert guard(out) == expected


# ── R15 EscalateIncident ───────────────────────────────────────────────────────────────────────────────────────
def test_r15_escalates_and_pages_the_owner(xw):
    d, out = act(xw, "EscalateIncident", {}, "D-1")
    assert out.code == "APPLIED", out
    assert xw.k.read("MATCH (i:Incident {id: 'i1'}) RETURN i.escalated, i.holds, i.escalated_seq") == [
        [True, 1, out.seq]]
    assert xw.k.read("MATCH (o:Outbox {pid: $p}) RETURN o.kind, o.target, o.incident, o.state",
                     {"p": envelope.pid_of("D-1")}) == [["page", "t1", "i1", "pending"]]
    assert keys(xw, d) == ["Incident:i1:state", f"Outbox:{envelope.pid_of('D-1')}"]
    assert act(xw, "EscalateIncident", {}, "D-2")[1].code == "APPLIED"  # escalating again adds a hold
    assert xw.k.read("MATCH (i:Incident {id: 'i1'}) RETURN i.holds") == [[2]]


def test_r15_needs_an_open_incident(xw):
    xw.ir.poke("MATCH (i:Incident {id: 'i1'}) SET i.state = 'resolved'")
    assert guard(act(xw, "EscalateIncident", {}, "D-1")[1]) == "incident_open"


def test_r15_pages_on_call_when_nobody_owns_the_service(xw):
    xw.ir.poke("MATCH (:Service {id: 'a'})-[o:OWNER_NOW]->() DELETE o")
    act(xw, "EscalateIncident", {}, "D-1")
    assert xw.k.read("MATCH (o:Outbox {kind: 'page'}) RETURN o.target") == [["on-call"]]


# ── R18 RestoreService ────────────────────────────────────────────────────────────────────────────────────────
def diverted(xw):
    xw.ir.poke("MATCH (s:Service {id: 'c'}) SET s.mode = 'isolated', s.health = 'down'")
    xw.ir.poke("MATCH (d:Deploy {id: 'c-bad'}) SET d.verified = true")


def test_r18_restores_a_diverted_service(xw_rollback):
    xw = xw_rollback
    diverted(xw)
    d, out = act(xw, "RestoreService", {"svc": "c"}, "D-1")
    assert out.code == "APPLIED", out
    assert xw.k.read("MATCH (s:Service {id: 'c'}) RETURN s.mode, s.active_replica, s.health, s.health_basis") == [
        ["normal", None, "ok", "restore"]]
    assert xw.k.read("MATCH (o:Outbox {pid: $p}) RETURN o.kind, o.target", {"p": envelope.pid_of("D-1")}) == [
        ["restore", "c"]]
    assert keys(xw, d) == ["Service:c:state", f"Outbox:{envelope.pid_of('D-1')}"]


@pytest.mark.parametrize(("setup", "expected"), [
    ("MATCH (i:Incident {id: 'i1'}) SET i.state = 'resolved'", "incident_open"),
    ("MATCH (s:Service {id: 'c'}) SET s.mode = 'normal'", "mode_diverted"),
    ("MATCH (d:Deploy {id: 'c-bad'}) SET d.verified = false", "verified"),
], ids=["incident-closed", "not-diverted", "deploy-unverified"])
def test_r18_guards(xw_rollback, setup, expected):
    xw = xw_rollback
    diverted(xw)
    xw.ir.poke(setup)
    assert guard(act(xw, "RestoreService", {"svc": "c"}, "D-1")[1]) == expected


def test_action_rules_are_decision_rules_and_only_r14_is_gated(xw):
    rules = {n: xw.k.registry.get(n) for n in ("IsolateService", "FailoverService", "RollbackDeploy",
                                                "EscalateIncident", "RestoreService")}
    assert all(r.requires_decision and r.cls == "action" and r.caller == ("executive",) for r in rules.values())
    assert {n for n, r in rules.items() if r.gated} == {"RollbackDeploy"}
    assert xw.k.read("MATCH (r:Rule) WHERE r.class = 'action' AND r.name <> 'NoopAction' AND r.name <> 'NoopActionGated' "
                     "RETURN r.name ORDER BY r.name") == [[n] for n in sorted(rules)]
