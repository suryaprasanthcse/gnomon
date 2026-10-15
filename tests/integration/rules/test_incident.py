"""R4 OpenIncident and R11 ResolveIncident (master §9.5, §9.12, §13.3 Incident)."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g2b]

TASKS = [{"id": "t1", "kind": "investigate", "skill": "ops"}, {"id": "t2", "kind": "comms", "skill": "triage"}]


def open_p(**over):
    p = {"alert": "al-1", "incident": "i1", "service": "a", "signature": "cpu", "tasks": TASKS}
    p.update(over)
    return p


def test_open_incident_happy_path(ir):
    ir.alert()
    out, delta = ir.delta("OpenIncident", open_p(), "triage-1", pid="oi-1")
    assert out.code == "APPLIED"
    assert delta == {("node", "create", "Entity/Incident", None), ("edge", "create", "TRIGGERED", None),
                     ("edge", "create", "AFFECTS", None), ("node", "create", "Entity/Task", None),
                     ("edge", "create", "PART_OF", None), ("edge", "create", "NEEDS", None)}
    assert ir.one("MATCH (:Alert {id: 'al-1'})-[:TRIGGERED]->(i:Incident:Entity {uid: 'inc:i1'})-[:AFFECTS]->"
                  "(s:Service) RETURN i.state, i.service, i.signature, i.escalated, i.holds, i.opened_seq, s.id") == \
        ["open", "a", "cpu", False, 0, out.seq, "a"]
    tasks = ir.k.read("MATCH (t:Task:Entity)-[:PART_OF]->(:Incident {id: 'i1'}) MATCH (t)-[:NEEDS]->(sk:Skill) "
                      "RETURN t.uid, t.kind, t.state, t.incident, sk.id ORDER BY t.uid")
    assert tasks == [["task:t1", "investigate", "open", "i1", "ops"], ["task:t2", "comms", "open", "i1", "triage"]]
    assert ir.write_keys("oi-1") == ["Incident:*", "Incident:i1:state", "Incident:a:cpu", "Task:*",
                                     "Alert:al-1:state"]


@pytest.mark.parametrize(
    ("over", "guard"),
    [({"alert": "nope"}, "G1:alert_on_service"), ({"service": "b"}, "G2:service_matches"),
     ({"signature": "mem"}, "G3:signature_matches"), ({"tasks": []}, "G4:has_tasks")],
)
def test_open_incident_alert_guards(ir, over, guard):
    ir.alert()
    assert ir.guard("OpenIncident", open_p(**over), "triage-1") == guard


def test_open_incident_identity_and_duplicate_guards(ir):
    ir.alert()
    ir.alert(source_id="al-2")
    ir.alert(source_id="al-3", service="b")
    ir.ok("OpenIncident", open_p(), "triage-1")
    # the same alert cannot trigger twice
    assert ir.guard("OpenIncident", open_p(incident="i2", tasks=[{"id": "t9", "kind": "k", "skill": "ops"}]),
                    "triage-1") == "G5:untriggered"
    # another alert, but the incident id is taken
    assert ir.guard("OpenIncident", open_p(alert="al-3", service="b", tasks=[{"id": "t9", "kind": "k",
                                                                             "skill": "ops"}]),
                    "triage-1") == "G6:new_incident_id"
    # another alert, new incident id, but a task id is taken
    assert ir.guard("OpenIncident", open_p(alert="al-3", service="b", incident="i3"), "triage-1") == \
        "G7:new_task_ids"
    # same service and signature while i1 is open
    assert ir.guard("OpenIncident", open_p(alert="al-2", incident="i4", tasks=[{"id": "t8", "kind": "k",
                                                                               "skill": "ops"}]),
                    "triage-1") == "G8:no_open_duplicate"


def test_open_incident_skill_guard(ir):
    ir.alert()
    assert ir.guard("OpenIncident", open_p(tasks=[{"id": "t1", "kind": "k", "skill": "juggling"}]), "triage-1") == \
        "G9:skills_exist"


def test_open_incident_caller(ir):
    ir.alert()
    assert ir.submit("OpenIncident", open_p(), "inv-1").code == "CALLER_DENIED"


# ── ResolveIncident ─────────────────────────────────────────────────────────

def ready_to_resolve(ir):
    ir.alert()
    ir.incident()
    ir.claim("t1", "inv-1", ttl=10_000)
    ir.ok("CompleteTask", {"task": "t1", "result": "found it"}, "inv-1")
    ir.ok("ProposeCause", {"incident": "i1", "cause": "dep:d1", "confidence": 80, "cites": []}, "inv-1")
    ir.poke("MATCH (s:Service {id: 'a'}) SET s.health = 'ok', s.mode = 'normal'")


def test_resolve_happy_path(ir):
    ready_to_resolve(ir)
    out, delta = ir.delta("ResolveIncident", {"incident": "i1"}, "inv-1", pid="res-1")
    assert out.code == "APPLIED"
    assert delta == {("node", "update", "Entity/Incident", "state"),
                     ("node", "update", "Entity/Incident", "resolved_seq")}
    assert ir.one("MATCH (i:Incident {id: 'i1'}) RETURN i.state, i.resolved_seq") == ["resolved", out.seq]
    assert ir.write_keys("res-1") == ["Incident:i1:state", "Incident:*"]


def test_resolve_guards(ir):
    ready_to_resolve(ir)
    assert ir.guard("ResolveIncident", {"incident": "nope"}, "inv-1") == "G1:incident_affects"
    ir.ok("Observe.health", {"source_id": "h-1", "service": "a", "health": "down", "observed_pt": ir.clock}, "ing-1")
    assert ir.guard("ResolveIncident", {"incident": "i1"}, "hum-1") == "G3:service_ok"
    ir.poke("MATCH (s:Service {id: 'a'}) SET s.health = 'ok', s.mode = 'isolated'")
    assert ir.guard("ResolveIncident", {"incident": "i1"}, "hum-1") == "G4:service_normal"
    ir.poke("MATCH (s:Service {id: 'a'}) SET s.mode = 'normal'")
    ir.ok("ResolveIncident", {"incident": "i1"}, "hum-1")
    assert ir.guard("ResolveIncident", {"incident": "i1"}, "inv-1") == "G2:incident_open"


def test_resolve_needs_every_task_done(ir):
    ir.alert()
    ir.incident(tasks=TASKS)
    ir.ok("ProposeCause", {"incident": "i1", "cause": "svc:a", "confidence": 50, "cites": []}, "inv-1")
    ir.poke("MATCH (s:Service {id: 'a'}) SET s.health = 'ok', s.mode = 'normal'")
    assert ir.guard("ResolveIncident", {"incident": "i1"}, "inv-1") == "G5:all_tasks_done"


def test_resolve_needs_a_current_root_cause(ir):
    ir.alert()
    ir.incident()
    ir.claim("t1", "inv-1", ttl=10_000)
    ir.ok("CompleteTask", {"task": "t1", "result": "r"}, "inv-1")
    ir.poke("MATCH (s:Service {id: 'a'}) SET s.health = 'ok', s.mode = 'normal'")
    assert ir.guard("ResolveIncident", {"incident": "i1"}, "inv-1") == "G6:root_cause_present"
    ir.ok("ProposeCause", {"incident": "i1", "cause": "dep:d1", "confidence": 60, "cites": []}, "inv-1",
          pid="pc-1")
    ir.ok("RetractFact.ROOT_CAUSE", {"s": "inc:i1", "o": "dep:d1", "fid": "pc-1#0", "mode": "void"}, "inv-1")
    assert ir.guard("ResolveIncident", {"incident": "i1"}, "inv-1") == "G6:root_cause_present"


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ([], "open"),
        (["resolve"], "resolved"),
    ],
    ids=["R4-open", "R11-resolved"],
)
def test_incident_state_machine(ir, steps, expected):
    ready_to_resolve(ir)
    for step in steps:
        ir.ok("ResolveIncident", {"incident": "i1"}, "hum-1")
    assert ir.one("MATCH (i:Incident {id: 'i1'}) RETURN i.state") == [expected]
