"""R1 Observe family (master §9.2, §11.1): alert, health and receipt; one test per guard probe."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g2b]

MAX_ATTEMPTS = 5


def alert_p(ir, **over):
    p = {"source_id": "al-9", "service": "a", "signature": "cpu", "severity": "high", "observed_pt": ir.clock,
         "payload_sha": "f" * 64}
    p.update(over)
    return p


def health_p(**over):
    p = {"source_id": "h-1", "service": "a", "health": "degraded", "observed_pt": 1_791_000_000_500}
    p.update(over)
    return p


def test_alert_happy_path(ir):
    out, delta = ir.delta("Observe.alert", alert_p(ir), "ing-1", pid="obs-a")
    assert out.code == "APPLIED"
    assert delta == {("node", "create", "Entity/Event", None), ("node", "create", "Alert/Entity", None),
                     ("edge", "create", "RAISED", None), ("edge", "create", "ON", None)}
    assert ir.one("MATCH (e:Event:Entity {uid: 'evt:al-9'})-[:RAISED]->(al:Alert:Entity {uid: 'alr:al-9'})-[:ON]->"
                  "(s:Service) RETURN e.kind, e.source_id, al.service, al.signature, al.severity, s.id") == \
        ["alert", "al-9", "a", "cpu", "high", "a"]
    assert ir.write_keys("obs-a") == ["Event:al-9", "Alert:*", "Alert:al-9:state"]


def test_alert_guards(ir):
    ir.ok("Observe.alert", alert_p(ir), "ing-1")
    assert ir.guard("Observe.alert", alert_p(ir), "ing-1") == "G1:new_event"
    assert ir.guard("Observe.alert", alert_p(ir, source_id="al-10", service="zz"), "ing-1") == "G2:service_exists"


def test_alert_callers(ir):
    assert ir.submit("Observe.alert", alert_p(ir), "triage-1").code == "CALLER_DENIED"


def test_health_happy_path(ir):
    out, delta = ir.delta("Observe.health", health_p(), "ing-1", pid="obs-h")
    assert out.code == "APPLIED"
    assert delta == {("node", "create", "Entity/Event", None), ("node", "update", "Entity/Service", "health"),
                     ("node", "update", "Entity/Service", "health_observed_pt"),
                     ("node", "update", "Entity/Service", "health_basis")}
    assert ir.one("MATCH (s:Service {id: 'a'}) RETURN s.health, s.health_observed_pt, s.health_basis") == \
        ["degraded", 1_791_000_000_500, "observed"]
    assert ir.write_keys("obs-h") == ["Event:h-1", "Service:a:state"]


@pytest.mark.parametrize(
    ("over", "guard"),
    [({"source_id": "h-1"}, "G1:new_event"), ({"source_id": "h-2", "service": "zz"}, "G2:service_exists"),
     ({"source_id": "h-3", "health": "melting"}, "G3:valid_health"),
     ({"source_id": "h-4", "observed_pt": 1_791_000_000_400}, "G4:newer_observation")],
)
def test_health_guards(ir, over, guard):
    ir.ok("Observe.health", health_p(), "ing-1")
    assert ir.guard("Observe.health", health_p(**over), "ing-1") == guard


@pytest.fixture
def outbox(opened):
    """A pending Outbox row written by PostUpdate."""
    ir = opened
    out = ir.ok("PostUpdate", {"incident": "i1", "message_id": "m1", "body": "investigating",
                               "cites": [{"s": "svc:a", "fid": ir.owned_by_fid()}]}, "com-1", pid="post-1")
    return ir, "post-1"


def receipt(ir, pid, sid, outcome):
    return {"source_id": sid, "outbox": pid, "outcome": outcome, "observed_pt": ir.clock}


def test_receipt_delivered(outbox):
    ir, pid = outbox
    out, delta = ir.delta("Observe.receipt", receipt(ir, pid, "r-1", "delivered"), "dsp-1", pid="rc-1")
    assert out.code == "APPLIED"
    assert delta == {("node", "create", "Entity/Event", None), ("node", "update", "Outbox", "attempts"),
                     ("node", "update", "Outbox", "state"), ("node", "update", "Outbox", "last_seq")}
    assert ir.one("MATCH (o:Outbox {pid: $pid}) RETURN o.state, o.attempts, o.last_seq", pid=pid) == \
        ["delivered", 1, out.seq]
    assert ir.write_keys("rc-1") == ["Event:r-1", f"Outbox:{pid}"]


def test_receipt_attempts_and_dead(outbox):
    ir, pid = outbox
    states = []
    for n in range(1, MAX_ATTEMPTS + 1):
        ir.ok("Observe.receipt", receipt(ir, pid, f"r-{n}", "failed"), "dsp-1")
        states.append(tuple(ir.one("MATCH (o:Outbox {pid: $pid}) RETURN o.state, o.attempts", pid=pid)))
    assert states == [("pending", 1), ("pending", 2), ("pending", 3), ("pending", 4), ("dead", 5)]
    assert ir.guard("Observe.receipt", receipt(ir, pid, "r-9", "delivered"), "dsp-1") == "G4:outbox_pending"


@pytest.mark.parametrize(
    ("sid", "outcome", "outbox_pid", "guard"),
    [("r-1", "delivered", None, "G1:new_event"), ("r-2", "lost", None, "G2:valid_outcome"),
     ("r-3", "delivered", "nope", "G3:outbox_exists")],
)
def test_receipt_guards(outbox, sid, outcome, outbox_pid, guard):
    ir, pid = outbox
    ir.ok("Observe.receipt", receipt(ir, pid, "r-1", "failed"), "dsp-1")
    assert ir.guard("Observe.receipt", receipt(ir, outbox_pid or pid, sid, outcome), "dsp-1") == guard


def test_receipt_of_delivered_outbox_is_refused(outbox):
    ir, pid = outbox
    ir.ok("Observe.receipt", receipt(ir, pid, "r-1", "delivered"), "dsp-1")
    assert ir.guard("Observe.receipt", receipt(ir, pid, "r-2", "failed"), "dsp-1") == "G4:outbox_pending"
