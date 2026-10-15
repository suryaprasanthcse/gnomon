"""R5 ClaimTask, R6 CompleteTask, R7 Handoff, R8 ExpireLease (master §9.6–§9.9, §13.3 Task and Lease)."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g2b]

MAX_TTL_MS = 900_000


def claim_p(agent="inv-1", task="t1", ttl=1_000):
    return {"agent": agent, "task": task, "ttl_ms": ttl}


def lease(ir, task="t1"):
    return ir.k.read("MATCH (a:Agent)-[l:HOLDS]->(:Task {id: $t}) RETURN a.id, l.from_seq, l.until_pt, l.closed_seq, "
                     "l.close_reason, l.from_agent ORDER BY l.from_seq", {"t": task})


def task_state(ir, task="t1"):
    return ir.one("MATCH (t:Task {id: $t}) RETURN t.state", t=task)[0]


# ── ClaimTask ───────────────────────────────────────────────────────────────

def test_claim_happy_path(opened):
    ir = opened
    out, delta = ir.delta("ClaimTask", claim_p(ttl=5_000), "inv-1", pid="cl-1")
    assert out.code == "APPLIED"
    assert delta == {("edge", "create", "HOLDS", None), ("node", "update", "Entity/Task", "state")}
    assert lease(ir) == [["inv-1", out.seq, ir.clock + 5_000, None, None, None]]
    assert task_state(ir) == "claimed"
    assert ir.write_keys("cl-1") == ["Task:t1:state", "HOLDS:*:t1", "Task:*"]


def test_claim_guards(opened):
    ir = opened
    assert ir.guard("ClaimTask", claim_p(agent="com-1"), "com-1") == "G1:skill_match"  # com-1 lacks ops
    assert ir.guard("ClaimTask", claim_p(agent="rsp-1"), "inv-1") == "G2:self_claim"
    assert ir.guard("ClaimTask", claim_p(ttl=0), "inv-1") == "G4:ttl_positive"
    assert ir.guard("ClaimTask", claim_p(ttl=MAX_TTL_MS + 1), "inv-1") == "G5:ttl_max"
    ir.ok("ClaimTask", claim_p(ttl=MAX_TTL_MS), "inv-1")
    assert ir.guard("ClaimTask", claim_p(agent="rsp-1"), "rsp-1") == "G3:task_open"


def test_claim_refused_while_a_live_lease_exists(opened):
    ir = opened
    ir.claim()
    ir.poke("MATCH (t:Task {id: 't1'}) SET t.state = 'open'")  # inconsistent on purpose: only the lease guard holds
    assert ir.guard("ClaimTask", claim_p(agent="rsp-1"), "rsp-1") == "G6:no_live_lease"


def test_claim_callers(opened):
    assert opened.submit("ClaimTask", claim_p(agent="ops-1"), "ops-1").code == "CALLER_DENIED"


# ── CompleteTask ────────────────────────────────────────────────────────────

def test_complete_happy_path(opened):
    ir = opened
    ir.claim()
    out, delta = ir.delta("CompleteTask", {"task": "t1", "result": "root cause found"}, "inv-1", pid="co-1")
    assert out.code == "APPLIED"
    assert delta == {("node", "update", "Entity/Task", "state"), ("node", "update", "Entity/Task", "result"),
                     ("node", "update", "Entity/Task", "done_seq"), ("edge", "update", "HOLDS", "closed_seq"),
                     ("edge", "update", "HOLDS", "close_reason")}
    assert ir.one("MATCH (t:Task {id: 't1'}) RETURN t.state, t.result, t.done_seq") == ["done", "root cause found",
                                                                                          out.seq]
    assert lease(ir)[0][3:5] == [out.seq, "completed"]
    assert ir.write_keys("co-1") == ["Task:t1:state", "HOLDS:*:t1", "Task:*"]


def test_complete_guards(opened):
    ir = opened
    assert ir.guard("CompleteTask", {"task": "t1", "result": "r"}, "inv-1") == "G1:holds_task"
    ir.claim(ttl=100)
    ir.ok("Handoff", {"task": "t1", "to": "rsp-1", "ttl_ms": 100}, "inv-1")
    assert ir.guard("CompleteTask", {"task": "t1", "result": "r"}, "inv-1") == "G2:lease_open"
    assert ir.guard("CompleteTask", {"task": "t1", "result": "r"}, "rsp-1", step=500) == "G3:lease_live"


def test_complete_requires_claimed_task(opened):
    ir = opened
    ir.claim(ttl=10_000)
    ir.poke("MATCH (t:Task {id: 't1'}) SET t.state = 'open'")
    assert ir.guard("CompleteTask", {"task": "t1", "result": "r"}, "inv-1") == "G4:task_claimed"


# ── Handoff ─────────────────────────────────────────────────────────────────

def test_handoff_happy_path(opened):
    ir = opened
    ir.claim(ttl=10_000)
    out, delta = ir.delta("Handoff", {"task": "t1", "to": "rsp-1", "ttl_ms": 2_000}, "inv-1", pid="ho-1")
    assert out.code == "APPLIED"
    assert delta == {("edge", "update", "HOLDS", "closed_seq"), ("edge", "update", "HOLDS", "close_reason"),
                     ("edge", "create", "HOLDS", None)}
    old, new = lease(ir)
    assert old[0] == "inv-1" and old[3:5] == [out.seq, "handoff"]
    assert new == ["rsp-1", out.seq, ir.clock + 2_000, None, None, "inv-1"]
    assert task_state(ir) == "claimed"
    assert ir.write_keys("ho-1") == ["HOLDS:*:t1"]


def test_handoff_guards(opened):
    ir = opened
    ho = lambda to="rsp-1", ttl=1_000: {"task": "t1", "to": to, "ttl_ms": ttl}  # noqa: E731
    assert ir.guard("Handoff", ho(), "inv-1") == "G1:holds_task"
    ir.claim(ttl=10_000)
    assert ir.guard("Handoff", ho(to="com-1"), "inv-1") == "G5:target_skilled"
    ir.ok("RegisterAgent", {"op": "deactivate", "id": "rsp-1"}, "ops-1")
    assert ir.guard("Handoff", ho(), "inv-1") == "G6:target_active"
    assert ir.guard("Handoff", ho(to="inv-1"), "inv-1") == "G7:target_other"
    assert ir.guard("Handoff", ho(to="tri-2", ttl=0), "inv-1") == "G8:ttl_positive"
    assert ir.guard("Handoff", ho(to="tri-2", ttl=MAX_TTL_MS + 1), "inv-1") == "G9:ttl_max"
    ir.ok("Handoff", ho(to="tri-2"), "inv-1")
    assert ir.guard("Handoff", ho(to="tri-2"), "inv-1") == "G2:lease_open"
    assert ir.guard("Handoff", ho(to="inv-1"), "tri-2", step=5_000) == "G3:lease_live"


def test_handoff_requires_claimed_task(opened):
    ir = opened
    ir.claim(ttl=10_000)
    ir.poke("MATCH (t:Task {id: 't1'}) SET t.state = 'open'")
    assert ir.guard("Handoff", {"task": "t1", "to": "rsp-1", "ttl_ms": 1_000}, "inv-1") == "G4:task_claimed"


# ── ExpireLease ─────────────────────────────────────────────────────────────

def test_expire_happy_path(opened):
    ir = opened
    ir.claim(ttl=100)
    out, delta = ir.delta("ExpireLease", {"task": "t1"}, "sch-1", pid="ex-1", step=200)
    assert out.code == "APPLIED"
    assert delta == {("edge", "update", "HOLDS", "closed_seq"), ("edge", "update", "HOLDS", "close_reason"),
                     ("node", "update", "Entity/Task", "state")}
    assert lease(ir)[0][3:5] == [out.seq, "expired"]
    assert task_state(ir) == "open"
    assert ir.write_keys("ex-1") == ["Task:t1:state", "HOLDS:*:t1", "Task:*"]


def test_expire_uses_the_rewrites_own_now(opened):
    """A lease that was live when the scheduler read it but has expired by apply time is still expired."""
    ir = opened
    ir.claim(ttl=100)
    claimed_at = ir.clock
    read = ir.k.read("MATCH (:Agent)-[l:HOLDS]->(:Task {id: 't1'}) WHERE l.closed_seq IS NULL "
                     "RETURN l.until_pt > $at", {"at": claimed_at + 50})
    assert read == [[True]]  # live at the scheduler's read
    assert ir.submit("ExpireLease", {"task": "t1"}, "sch-1", at=claimed_at + 150).code == "APPLIED"


def test_expire_guards(opened):
    ir = opened
    assert ir.guard("ExpireLease", {"task": "t1"}, "sch-1") == "G1:lease_on_task"
    ir.claim(ttl=1_000)
    assert ir.guard("ExpireLease", {"task": "t1"}, "sch-1") == "G3:lease_expired"
    ir.ok("CompleteTask", {"task": "t1", "result": "r"}, "inv-1")
    assert ir.guard("ExpireLease", {"task": "t1"}, "sch-1", step=5_000) == "G2:lease_open"


def test_expire_requires_claimed_task(opened):
    ir = opened
    ir.claim(ttl=100)
    ir.poke("MATCH (t:Task {id: 't1'}) SET t.state = 'open'")
    assert ir.guard("ExpireLease", {"task": "t1"}, "sch-1", step=500) == "G4:task_claimed"


def test_expire_is_scheduler_only(opened):
    ir = opened
    ir.claim(ttl=100)
    assert ir.submit("ExpireLease", {"task": "t1"}, "inv-1", step=500).code == "CALLER_DENIED"


# ── State machines (§13.3) ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("steps", "task", "leases"),
    [
        ([], "open", []),
        (["claim"], "claimed", [("inv-1", None)]),
        (["claim", "complete"], "done", [("inv-1", "completed")]),
        (["claim", "handoff"], "claimed", [("inv-1", "handoff"), ("rsp-1", None)]),
        (["claim", "expire"], "open", [("inv-1", "expired")]),
        (["claim", "expire", "reclaim"], "claimed", [("inv-1", "expired"), ("rsp-1", None)]),
        (["claim", "handoff", "complete-by-target"], "done", [("inv-1", "handoff"), ("rsp-1", "completed")]),
    ],
    ids=["R4", "R5", "R5-R6", "R5-R7", "R5-R8", "R5-R8-R5", "R5-R7-R6"],
)
def test_task_and_lease_state_machines(opened, steps, task, leases):
    ir = opened
    actions = {
        "claim": lambda: ir.claim(ttl=100),
        "complete": lambda: ir.ok("CompleteTask", {"task": "t1", "result": "r"}, "inv-1"),
        "handoff": lambda: ir.ok("Handoff", {"task": "t1", "to": "rsp-1", "ttl_ms": 1_000}, "inv-1"),
        "expire": lambda: ir.ok("ExpireLease", {"task": "t1"}, "sch-1", step=500),
        "reclaim": lambda: ir.claim(agent="rsp-1", ttl=100),
        "complete-by-target": lambda: ir.ok("CompleteTask", {"task": "t1", "result": "r"}, "rsp-1"),
    }
    for step in steps:
        actions[step]()
    assert task_state(ir) == task
    assert [(row[0], row[4]) for row in lease(ir)] == leases
