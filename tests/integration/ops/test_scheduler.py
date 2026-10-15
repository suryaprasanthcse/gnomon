"""§11.3 lease scheduler: deterministic pids, wall time selects, the rewrite's own now guards."""

import pytest

from gnomon.ops.scheduler import Scheduler
from tests.support.sim_world import Clock

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4b]


def lease(ir, task="t1"):
    return ir.one("MATCH (:Agent)-[l:HOLDS]->(:Task {id: $t}) WHERE l.closed_seq IS NULL RETURN l.from_seq, l.until_pt",
                  t=task)


def test_scheduler_deterministic_pids(opened):
    ir = opened
    ir.claim("t1", agent="inv-1", ttl=1_000)
    from_seq, until = lease(ir)
    sched = Scheduler(ir.k, Clock(until + 5), agent="sch-1")
    ((pid, out),) = sched.tick()
    assert pid == f"exp:t1:{from_seq}" and out.code == "APPLIED"
    assert ir.one("MATCH (t:Task {id: 't1'}) RETURN t.state") == ["open"]
    assert sched.tick() == []  # nothing left to expire
    ir.claim("t1", agent="rsp-1", ttl=1_000)
    from_seq2, until2 = lease(ir)
    assert from_seq2 != from_seq
    ((pid2, out2),) = Scheduler(ir.k, Clock(until2 + 5), agent="sch-1").tick()
    assert pid2 == f"exp:t1:{from_seq2}" and out2.code == "APPLIED"


def test_scheduler_uses_wall_then_guard_uses_now(opened):
    ir = opened
    ir.claim("t1", agent="inv-1", ttl=1_000)
    from_seq, until = lease(ir)
    assert Scheduler(ir.k, Clock(until - 1), agent="sch-1").tick() == []  # wall time has not reached the lease
    early = ir.submit("ExpireLease", {"task": "t1"}, "sch-1", at=until - 1)
    assert early.code == "REJECTED_GUARD"  # the guard reads the rewrite's own now, not the caller's wall
    ((pid, out),) = Scheduler(ir.k, Clock(until), agent="sch-1").tick()
    assert (pid, out.code) == (f"exp:t1:{from_seq}", "APPLIED")


def test_scheduler_skips_live_and_completed_leases(opened):
    ir = opened
    ir.claim("t1", agent="inv-1", ttl=100_000)
    _, until = lease(ir)
    assert Scheduler(ir.k, Clock(until - 50_000), agent="sch-1").tick() == []
    ir.ok("CompleteTask", {"task": "t1", "result": "done"}, "inv-1")
    assert Scheduler(ir.k, Clock(until + 1), agent="sch-1").tick() == []
