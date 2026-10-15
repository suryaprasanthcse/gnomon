"""§10.2 EN-R4..EN-R11 on hand-built states: every valid tuple appears, no invalid one does."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4a]


def listed(qs, agent, rule, args=None):
    return qs.list_actions(qs.session(agent), rule, args).tuples


def field(tuples, name):
    return [t[name] for t in tuples]


def test_tuples_are_indexed_in_order(opened, qs):
    ir = opened
    ir.alert(source_id="al-2", service="b", signature="cpu")
    ir.alert(source_id="al-3", service="b", signature="mem")
    tuples = listed(qs, "triage-1", "OpenIncident")
    assert [t["index"] for t in tuples] == [0, 1]
    assert field(tuples, "alert") == ["al-2", "al-3"]


def test_en_r4_untriggered_alerts_without_an_open_duplicate(opened, qs):
    ir = opened  # al-1 triggered i1 (service a, cpu)
    ir.alert(source_id="al-2", service="b", signature="cpu")
    ir.alert(source_id="al-3", service="a", signature="mem")
    ir.alert(source_id="al-4", service="a", signature="cpu")  # an open incident already covers (a, cpu)
    tuples = listed(qs, "triage-1", "OpenIncident")
    assert [(t["alert"], t["service"], t["signature"]) for t in tuples] == [("al-2", "b", "cpu"), ("al-3", "a", "mem")]
    assert set(tuples[0]) >= {"index", "alert", "service", "signature", "severity"}
    ir.incident(incident="i2", alert="al-2", service="b", signature="cpu", tasks=[{"id": "t2", "kind": "x", "skill": "ops"}])
    assert field(listed(qs, "triage-1", "OpenIncident"), "alert") == ["al-3"]


def test_en_r5_open_tasks_the_caller_is_skilled_for(opened, qs):
    ir = opened
    ir.alert(source_id="al-2", service="b", signature="cpu")
    ir.incident(incident="i2", alert="al-2", service="b", signature="cpu",
                tasks=[{"id": "t2", "kind": "x", "skill": "ops"}, {"id": "t3", "kind": "y", "skill": "triage"}])
    assert field(listed(qs, "inv-1", "ClaimTask"), "task") == ["t1", "t2"]  # inv-1 holds skill ops
    assert field(listed(qs, "com-1", "ClaimTask"), "task") == ["t3"]  # com-1 holds skill triage
    assert field(listed(qs, "tri-2", "ClaimTask"), "task") == ["t1", "t2", "t3"]
    assert listed(qs, "ing-1", "ClaimTask") == []  # no skills
    ir.claim("t1", agent="inv-1", ttl=100_000)
    assert field(listed(qs, "rsp-1", "ClaimTask"), "task") == ["t2"]  # a claimed task is no longer open
    first = listed(qs, "inv-1", "ClaimTask")[0]
    assert (first["kind"], first["incident"]) == ("x", "i2")


def test_en_r6_live_leases_of_the_caller(opened, qs):
    ir = opened
    assert listed(qs, "inv-1", "CompleteTask") == []
    ir.claim("t1", agent="inv-1", ttl=1_000)
    (tup,) = listed(qs, "inv-1", "CompleteTask")
    assert tup["task"] == "t1" and tup["lease_until"] == ir.clock + 1_000
    assert listed(qs, "rsp-1", "CompleteTask") == []
    ir.ok("RegisterEntity.Team", {"id": "t9", "name": "t9"}, "ops-1", step=5_000)  # Head.pt moves past the lease
    assert listed(qs, "inv-1", "CompleteTask") == []


def test_en_r7_handoff_pairs(opened, qs):
    ir = opened
    ir.claim("t1", agent="inv-1", ttl=100_000)
    assert [(t["task"], t["to"]) for t in listed(qs, "inv-1", "Handoff")] == [
        ("t1", "ops-1"), ("t1", "rsp-1"), ("t1", "tri-2")]
    assert listed(qs, "rsp-1", "Handoff") == []  # rsp-1 holds no lease
    ir.ok("RegisterAgent", {"op": "deactivate", "id": "rsp-1"}, "ops-1")
    assert field(listed(qs, "inv-1", "Handoff"), "to") == ["ops-1", "tri-2"]


def test_en_r9_citeable_live_facts_of_an_open_incident(opened, qs):
    ir = opened
    fid = ir.owned_by_fid()
    rows = listed(qs, "com-1", "PostUpdate", {"incident": "i1"})
    assert [(t["rel"], t["s"], t["o"], t["fid"]) for t in rows] == [("OWNED_BY", "svc:a", "team:t1", fid)]
    ir.ok("AssertFact.DEPLOYED", {"s": "svc:a", "o": "dep:d1", "vf": ir.clock, "supersede": False}, "ops-1")
    ir.ok("ProposeCause", {"incident": "i1", "cause": "dep:d1", "confidence": 80, "cites": []}, "inv-1", pid="pc-1")
    rels = sorted((t["rel"], t["s"], t["o"]) for t in listed(qs, "com-1", "PostUpdate", {"incident": "i1"}))
    assert rels == [("DEPLOYED", "svc:a", "dep:d1"), ("OWNED_BY", "svc:a", "team:t1"), ("ROOT_CAUSE", "inc:i1", "dep:d1")]
    assert listed(qs, "com-1", "PostUpdate", {"incident": "nope"}) == []


def test_en_r10_deploys_in_the_window_not_yet_proposed(opened, qs):
    ir = opened
    assert listed(qs, "inv-1", "ProposeCause", {"incident": "i1"}) == []  # no DEPLOYED fact yet
    ir.ok("AssertFact.DEPLOYED", {"s": "svc:a", "o": "dep:d1", "vf": ir.clock, "supersede": False}, "ops-1")
    (tup,) = listed(qs, "inv-1", "ProposeCause", {"incident": "i1"})
    assert (tup["incident"], tup["cause"], tup["version"]) == ("i1", "dep:d1", "1")
    ir.ok("ProposeCause", {"incident": "i1", "cause": "dep:d1", "confidence": 60, "cites": []}, "inv-1")
    assert listed(qs, "inv-1", "ProposeCause", {"incident": "i1"}) == []  # the current root cause is not re-offered


def test_en_r11_only_incidents_meeting_every_guard(opened, qs):
    ir = opened
    assert listed(qs, "inv-1", "ResolveIncident") == []  # task open, no root cause
    ir.claim("t1", agent="inv-1", ttl=100_000)
    ir.ok("CompleteTask", {"task": "t1", "result": "ok"}, "inv-1")
    assert listed(qs, "inv-1", "ResolveIncident") == []  # no root cause yet
    ir.ok("AssertFact.DEPLOYED", {"s": "svc:a", "o": "dep:d1", "vf": ir.clock, "supersede": False}, "ops-1")
    ir.ok("ProposeCause", {"incident": "i1", "cause": "dep:d1", "confidence": 60, "cites": []}, "inv-1")
    assert field(listed(qs, "inv-1", "ResolveIncident"), "incident") == ["i1"]
    ir.poke("MATCH (s:Service {id: 'a'}) SET s.health = 'down'")
    assert listed(qs, "inv-1", "ResolveIncident") == []  # the service is not ok
    ir.poke("MATCH (s:Service {id: 'a'}) SET s.health = 'ok'")
    ir.ok("ResolveIncident", {"incident": "i1"}, "inv-1")
    assert listed(qs, "inv-1", "ResolveIncident") == []


def test_open_parameter_rules_require_their_args(opened, qs):
    from gnomon.query.service import QueryError

    sess = qs.session("com-1")
    for rule in ("PostUpdate", "ProposeCause"):
        with pytest.raises(QueryError):
            qs.list_actions(sess, rule)
    with pytest.raises(QueryError):
        qs.list_actions(sess, "ExpireLease")  # scheduler-only: not an agent-callable enablement
    with pytest.raises(QueryError):
        qs.list_actions(sess, "NoSuchRule")
