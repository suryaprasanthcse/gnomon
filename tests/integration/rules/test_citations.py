"""R9 PostUpdate and R10 ProposeCause (master §9.10, §9.11): live citations, root causes, confidence bounds."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g2b]

EPOCH = 1_791_000_000_000


def post_p(ir, **over):
    p = {"incident": "i1", "message_id": "m1", "body": "we are investigating", "cites": [{"s": "svc:a",
                                                                                          "fid": ir.owned_by_fid()}]}
    p.update(over)
    return p


def test_post_update_with_a_live_cite(opened):
    ir = opened
    fid = ir.owned_by_fid()
    out, delta = ir.delta("PostUpdate", post_p(ir), "com-1", pid="pu-1")
    assert out.code == "APPLIED"
    assert delta == {("node", "create", "Entity/Message", None), ("edge", "create", "ABOUT", None),
                     ("edge", "create", "CITES", None), ("node", "create", "Outbox", None)}
    assert ir.one("MATCH (m:Message:Entity {uid: 'msg:m1'})-[:ABOUT]->(i:Incident) MATCH (m)-[c:CITES]->(e:Entity) "
                  "RETURN m.body, m.author, m.incident, c.fid, e.uid") == \
        ["we are investigating", "com-1", "i1", fid, "svc:a"]
    assert ir.one("MATCH (o:Outbox {pid: 'pu-1'}) RETURN o.kind, o.target, o.incident, o.payload, o.state, "
                  "o.attempts") == ["post_update", "status-page", "i1", "we are investigating", "pending", 0]
    assert ir.write_keys("pu-1") == ["Message:*", "Outbox:pu-1"]


def test_post_update_rejects_a_retracted_cite(opened):
    ir = opened
    fid = ir.owned_by_fid()
    ir.ok("RetractFact.OWNED_BY", {"s": "svc:a", "o": "team:t1", "fid": fid, "mode": "end", "vt_end": ir.clock},
          "ops-1")
    assert ir.guard("PostUpdate", post_p(ir, cites=[{"s": "svc:a", "fid": fid}]), "com-1") == "G4:cites_live"


def test_post_update_guards(opened):
    ir = opened
    assert ir.guard("PostUpdate", post_p(ir, incident="nope"), "com-1") == "G1:incident_exists"
    assert ir.guard("PostUpdate", post_p(ir, cites=[]), "com-1") == "G3:has_cites"
    assert ir.guard("PostUpdate", post_p(ir, cites=[{"s": "svc:a", "fid": "bogus#0"}]), "com-1") == "G4:cites_live"
    ir.poke("MATCH (i:Incident {id: 'i1'}) SET i.state = 'resolved'")
    assert ir.guard("PostUpdate", post_p(ir), "com-1") == "G2:incident_open"


def test_post_update_is_comms_only(opened):
    ir = opened
    assert ir.submit("PostUpdate", post_p(ir), "inv-1").code == "CALLER_DENIED"


def cause_p(**over):
    p = {"incident": "i1", "cause": "dep:d1", "confidence": 75, "cites": []}
    p.update(over)
    return p


def test_propose_cause_happy_path(opened):
    ir = opened
    out, delta = ir.delta("ProposeCause", cause_p(), "inv-1", pid="pc-1")
    assert out.code == "APPLIED"
    assert delta == {("edge", "create", "ROOT_CAUSE", None)}
    inf = 9007199254740991
    assert ir.one("MATCH (:Incident {id: 'i1'})-[rc:ROOT_CAUSE]->(x:Deploy) RETURN rc.fid, rc.vf, rc.vt, rc.ts, rc.te, "
                  "rc.by, rc.confidence, x.id") == ["pc-1#0", ir.clock, inf, out.seq, inf, "inv-1", 75, "d1"]
    assert ir.write_keys("pc-1") == ["ROOT_CAUSE:inc:i1:*", "ROOT_CAUSE:*:dep:d1", "ROOT_CAUSE:*"]


def test_propose_cause_with_live_cites(opened):
    ir = opened
    out = ir.submit("ProposeCause", cause_p(cause="svc:a", cites=[{"s": "svc:a", "fid": ir.owned_by_fid()}]), "inv-1")
    assert out.code == "APPLIED"


@pytest.mark.parametrize(
    ("over", "guard"),
    [({"cause": "dep:nope"}, "G1:incident_and_cause_exist"), ({"incident": "nope"}, "G1:incident_and_cause_exist"),
     ({"cause": "team:t1"}, "G3:cause_typed"), ({"confidence": -1}, "G4:confidence_min"),
     ({"confidence": 101}, "G5:confidence_max"), ({"cites": [{"s": "svc:a", "fid": "bogus#0"}]}, "G7:cites_live")],
)
def test_propose_cause_guards(opened, over, guard):
    assert opened.guard("ProposeCause", cause_p(**over), "inv-1") == guard


def test_propose_cause_bounds_are_inclusive(opened):
    ir = opened
    assert ir.submit("ProposeCause", cause_p(confidence=0), "inv-1").code == "APPLIED"
    assert ir.submit("ProposeCause", cause_p(cause="svc:a", confidence=100), "inv-1").code == "APPLIED"


def test_duplicate_current_root_cause_rejected(opened):
    ir = opened
    ir.ok("ProposeCause", cause_p(), "inv-1")
    assert ir.guard("ProposeCause", cause_p(confidence=90), "inv-1") == "G6:not_already_current"


def test_propose_cause_needs_an_open_incident(opened):
    ir = opened
    ir.poke("MATCH (i:Incident {id: 'i1'}) SET i.state = 'resolved'")
    assert ir.guard("ProposeCause", cause_p(), "inv-1") == "G2:incident_open"
