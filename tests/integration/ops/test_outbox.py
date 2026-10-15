"""§11.1 outbox state machine and §11.2 dispatcher loop: pid as idempotency key, deterministic receipt source ids."""

import pytest

from gnomon.ops.dispatcher import Dispatcher
from gnomon.ops.sweeper import Sweeper
from gnomon.sim.infra import Infra
from tests.support.sim_world import EPOCH, Clock

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4b]


@pytest.fixture
def post(opened):
    """An outbox row `pu-1` written by PostUpdate (state pending, attempts 0), a dispatcher and its infrastructure."""
    ir = opened
    fid = ir.owned_by_fid()
    ir.ok("PostUpdate", {"incident": "i1", "message_id": "m1", "body": "we are investigating",
                         "cites": [{"s": "svc:a", "fid": fid}]}, "com-1", pid="pu-1")
    infra = Infra(seed=1)
    clock = Clock(ir.clock + 1_000)
    return ir, infra, Dispatcher(ir.k, infra, clock, agent="dsp-1"), clock


def outbox(ir, pid="pu-1"):
    return ir.one("MATCH (o:Outbox {pid: $pid}) RETURN o.state, o.attempts", pid=pid)


def test_outbox_state_machine(post):
    ir, infra, disp, clock = post
    assert outbox(ir) == ["pending", 0]
    (done,) = disp.tick()
    assert done["pid"] == "pu-1" and done["receipt"].code == "APPLIED"
    assert outbox(ir) == ["delivered", 1]
    assert disp.tick() == []  # delivered rows are never read again


def test_failures_retry_until_delivered_or_dead(post):
    ir, infra, disp, clock = post
    infra.drop_next(2)
    for expected in (["pending", 1], ["pending", 2], ["delivered", 3]):
        disp.tick()
        clock.step(1_000)
        assert outbox(ir) == expected

    ir.ok("PostUpdate", {"incident": "i1", "message_id": "m2", "body": "again",
                         "cites": [{"s": "svc:a", "fid": ir.owned_by_fid()}]}, "com-1", pid="pu-2")
    infra.drop_next(5)
    for n in range(1, 6):
        disp.tick()
        clock.step(1_000)
        assert outbox(ir, "pu-2") == (["pending", n] if n < 5 else ["dead", 5])
    assert disp.tick() == []
    assert Sweeper(ir.k, clock).sweep()["dead_outbox"] == ["pu-2"]


def test_dispatcher_idempotent_by_pid(post):
    ir, infra, disp, clock = post
    infra.duplicate_next(1)  # the delivery reaches the infrastructure twice
    disp.tick()
    assert infra.apply_count("pu-1") == 1
    again = infra.deliver({"pid": "pu-1", "kind": "post_update", "target": "status-page", "incident": "i1",
                           "payload": "we are investigating", "attempts": 1})
    assert again["outcome"] == "delivered" and infra.apply_count("pu-1") == 1
    restarted = Dispatcher(ir.k, infra, clock, agent="dsp-1")
    assert restarted.tick() == []
    assert ir.one("MATCH (r:Rewrite) WHERE r.rule = 'Observe.receipt' RETURN count(r)") == [1]


def test_receipt_source_ids_deterministic(post):
    ir, infra, disp, clock = post
    infra.drop_next(1)
    disp.tick()
    clock.step(1_000)
    disp.tick()
    ids = [r[0] for r in ir.k.read("MATCH (e:Event {kind: 'receipt'}) RETURN e.source_id ORDER BY e.source_id")]
    assert ids == ["rcpt:pu-1:0", "rcpt:pu-1:1"]


def test_sweeper_surfaces_unclosed_expired_leases(opened):
    ir = opened
    ir.claim("t1", agent="inv-1", ttl=1_000)
    until = ir.one("MATCH (:Agent)-[l:HOLDS]->(:Task {id: 't1'}) RETURN l.until_pt")[0]
    assert Sweeper(ir.k, Clock(until - 1)).sweep()["expired_leases"] == []
    assert Sweeper(ir.k, Clock(until)).sweep()["expired_leases"] == ["t1"]
    assert Sweeper(ir.k, Clock(until)).sweep()["dead_outbox"] == [] and EPOCH
