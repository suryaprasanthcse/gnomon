"""A16 · gate ASSURE · the chaos suite (master §22 "Federation chaos" and the Core fault list), on the three-tenant world.

Each test breaks something the system promises to survive, then checks the promise and that the whole federation still
audits green: K1-K8 on every tenant (K4 replays each log to the live hash) and K9 across the tenants.

    agent kill            a lease holder stops; the lease expires; another agent finishes; the dead agent's proposal is a DUPLICATE
    dispatcher drop       an outbox row is dropped twice, then delivered; a row dropped five times is dead and surfaced
    dispatcher duplicate  the infrastructure receives a delivery twice and applies it once; a restarted dispatcher does nothing
    relay crash           the relay dies at each of its four crash points; a new relay resumes with exactly one receipt each
    clock skew            a sender whose clock runs ahead is quarantined; every other channel keeps flowing
    delayed health        a health recovery that lands while the consumer is deciding makes the decision stale, and it
                          re-simulates against the recovered upstream (master §18, step 6)
"""

import pytest

from tests.integration.test_a13_federation import TENANTS, TRIAGE
from tests.support.sim_world import Clock

pytestmark = [pytest.mark.chaos, pytest.mark.integration, pytest.mark.gate_assure]


def assert_green(aw) -> None:
    reports, federation = aw.full_audit()
    for tenant, report in reports.items():
        assert report.ok, (tenant, report.as_dict())
    assert federation.ok, federation.as_dict()


def count(aw, t, cypher, **params):
    return aw.rows(t, cypher, **params)[0][0]


# ── agent kill

def test_a_killed_lease_holder_is_replaced_and_its_replayed_proposal_is_a_duplicate(aw):
    from gnomon.ops.scheduler import Scheduler

    ir = aw.sync("A")
    first = ir.claim("t1", "inv-1", ttl=1_000, pid="kill-claim")
    assert first.code == "APPLIED"
    held = ir.clock
    # inv-1 is killed here: it never completes, never renews. The lease is still open in the log.
    assert count(aw, "A", "MATCH (:Agent)-[l:HOLDS]->(:Task {id: 't1'}) WHERE l.closed_seq IS NULL RETURN count(l)") == 1
    early = Scheduler(aw.k["A"], lambda: held).tick()
    assert early == []  # nothing has expired yet: wall time only selects candidates
    done = Scheduler(aw.k["A"], lambda: held + 5_000).tick()
    assert [out.code for _, out in done] == ["APPLIED"]
    assert count(aw, "A", "MATCH (:Agent)-[l:HOLDS]->(:Task {id: 't1'}) WHERE l.closed_seq IS NULL RETURN count(l)") == 0
    assert aw.sync("A").claim("t1", "tri-2", ttl=60_000, pid="takeover").code == "APPLIED"
    aw.ir_ok("A", "CompleteTask", {"task": "t1", "result": "finished by the second agent"}, "tri-2")
    replayed = aw.ir_try("A", "ClaimTask", {"agent": "inv-1", "task": "t1", "ttl_ms": 1_000}, "inv-1", pid="kill-claim")
    assert replayed.code == "DUPLICATE" and replayed.seq == first.seq
    assert [out.code for _, out in Scheduler(aw.k["A"], lambda: held + 5_000).tick()] == []  # nothing is left to expire
    assert_green(aw)


# ── dispatcher

def post(aw, t="A", pid="pu-chaos", mid="m-chaos"):
    ir = aw.sync(t)
    ir.ok("PostUpdate", {"incident": "i1", "message_id": mid, "body": "we are investigating",
                         "cites": [{"s": "svc:a", "fid": ir.owned_by_fid()}]}, "com-1", pid=pid)
    return ir


def outbox(aw, t, pid):
    return aw.rows(t, "MATCH (o:Outbox {pid: $pid}) RETURN o.state, o.attempts", pid=pid)[0]


def test_dispatcher_drops_are_retried_to_delivery_and_a_hopeless_row_is_surfaced_dead(aw):
    from gnomon.ops.dispatcher import Dispatcher
    from gnomon.ops.sweeper import Sweeper
    from gnomon.sim.infra import Infra

    ir = post(aw)
    infra, clock = Infra(seed=1), Clock(ir.clock + 1_000)
    dispatcher = Dispatcher(aw.k["A"], infra, clock, agent="dsp-1")
    infra.drop_next(2)
    for expected in (["pending", 1], ["pending", 2], ["delivered", 3]):
        dispatcher.tick()
        clock.step(1_000)
        assert outbox(aw, "A", "pu-chaos") == expected
    assert infra.apply_count("pu-chaos") == 1
    post(aw, pid="pu-doomed", mid="m-doomed")
    infra.drop_next(5)
    for n in range(1, 6):
        dispatcher.tick()
        clock.step(1_000)
    assert outbox(aw, "A", "pu-doomed") == ["dead", 5]
    assert Sweeper(aw.k["A"], clock).sweep()["dead_outbox"] == ["pu-doomed"]
    assert_green(aw)


def test_a_duplicated_delivery_is_applied_once_and_a_restarted_dispatcher_has_nothing_to_do(aw):
    from gnomon.ops.dispatcher import Dispatcher
    from gnomon.sim.infra import Infra

    ir = post(aw, "B")
    infra, clock = Infra(seed=2), Clock(ir.clock + 1_000)
    Dispatcher(aw.k["B"], infra, clock, agent="dsp-1").tick()
    infra.duplicate_next(1)
    post(aw, "B", pid="pu-twice", mid="m-twice")
    Dispatcher(aw.k["B"], infra, clock, agent="dsp-1").tick()
    assert infra.apply_count("pu-twice") == 1 and infra.apply_count("pu-chaos") == 1
    assert Dispatcher(aw.k["B"], infra, clock, agent="dsp-1").tick() == []
    assert count(aw, "B", "MATCH (r:Rewrite) WHERE r.rule = 'Observe.receipt' RETURN count(r)") == 2
    assert_green(aw)


# ── relay crash

@pytest.mark.parametrize("point", ["before_receive", "after_receive", "before_ack", "after_ack"])
def test_a_relay_that_dies_at_any_point_is_replaced_and_every_message_lands_exactly_once(aw, point):
    from gnomon.federation.relay_protocol import RelayCrash

    aw.notice("A", "B", "one", pid="cx1")
    aw.notice("A", "B", "two", pid="cx2")
    aw.consume("C", "A", "pay-db", "billing")
    first = aw.relay()
    first.crash_at = point
    with pytest.raises(RelayCrash):
        first.deliver("A", "B")
    fresh = aw.relay()
    fresh.deliver("A", "B")
    fresh.deliver("C", "A")
    fresh.sweep()
    assert [r[4] for r in aw.xmsgs("A", "x.chan = 'out:B'")] == ["delivered", "delivered"]
    assert count(aw, "B", "MATCH (r:Rewrite) WHERE r.id STARTS WITH 'rx:A/' RETURN count(r)") == 2  # one receipt each
    assert count(aw, "A", "MATCH (c:Consumer) RETURN count(c)") == 1
    assert_green(aw)


def test_two_relays_racing_one_channel_still_leave_one_receipt_per_message(aw):
    aw.notice("A", "B", "one", pid="rc1")
    aw.notice("A", "B", "two", pid="rc2")
    a, b = aw.relay(), aw.relay()
    a.deliver("A", "B", limit=1)
    b.deliver("A", "B")
    a.deliver("A", "B")
    assert count(aw, "B", "MATCH (r:Rewrite) WHERE r.id STARTS WITH 'rx:A/' RETURN count(r)") == 2
    assert_green(aw)


# ── clock skew

def test_a_skewed_sender_is_quarantined_and_the_other_channels_keep_flowing(aw):
    import time

    ahead = int(time.time() * 1000) + 10 * 60_000
    aw.sub("A", "SendNotice", {"peer": "B", "incident": "i1", "body": "from the future"}, TRIAGE, pid="skew-1", at=ahead)
    aw.notice("C", "B", "from a healthy clock")
    aw.notice("B", "C", "also healthy")
    live = aw.relay(mode="live")
    report = live.deliver("A", "B")
    assert report.stopped == "SKEW_QUARANTINED" and ("A", "B") in live.quarantined
    assert live.deliver("A", "B").applied == []  # stays quarantined
    assert live.deliver("C", "B").applied and live.deliver("B", "C").applied  # senders with sane clocks flow
    assert aw.xmsgs("A", "x.chan = 'out:B'")[0][4] == "queued"  # the skewed message was never consumed
    assert count(aw, "B", "MATCH (x:XMsg {chan: 'in:A'}) RETURN count(x)") == 0
    assert_green(aw)


# ── delayed health

def test_an_older_health_observation_arriving_late_does_not_overwrite_a_newer_one(aw):
    ir = aw.sync("A")
    newer = ir.clock + 500
    aw.ir_ok("A", "Observe.health", {"source_id": "h-new", "service": "a", "health": "down", "observed_pt": newer}, "ing-1")
    late = aw.ir_try("A", "Observe.health", {"source_id": "h-old", "service": "a", "health": "ok", "observed_pt": newer - 400},
                     "ing-1")
    assert late.code in ("APPLIED", "REJECTED_GUARD"), late
    assert aw.rows("A", "MATCH (s:Service {id: 'a'}) RETURN s.health") == [["down"]]  # the newer observation wins
    assert_green(aw)


def test_a_health_recovery_that_lands_mid_decision_makes_the_decision_stale_and_it_re_simulates(aw):
    relay = aw.relay()
    aw.consume("B", "A", "pay", "a", hard=True)  # B's service a depends hard on A's pay
    relay.deliver("B", "A")
    aw.health("A", "pay", "down")
    relay.publish_health("A")
    relay.deliver("A", "B")
    assert aw.rows("B", "MATCH (x:ExternalService {remote_id: 'pay'}) RETURN x.health") == [["down"]]
    landed = []

    def recovery_arrives(executive, d):
        if not landed:
            landed.append(d.attempt)
            aw.health("A", "pay", "ok")
            relay.publish_health("A")
            assert relay.deliver("A", "B").applied  # the delayed recovery reaches B while it is deciding
            aw.sync("B")  # the Executive's injected clock follows the log: the receipt moved B's Head forward

    ex = aw.xws["B"].executive(federation=True, before_commit=recovery_arrives)
    aw.sync("B")
    res = ex.decide("i1", [], "upstream is down", "chaos-1")
    assert landed == [1] and res.stale_flushes == 1 and res.attempt == 2, (res.trace, res.state, res.error, res.outcome)
    assert "ext:A.pay" in res.base_output["region"]
    ext = [row for row in res.base_output["impacted"] if row[0] == "ext:A.pay"]
    assert ext == []  # the re-simulation saw the recovered upstream: the proxy is no longer a source
    assert aw.rows("B", "MATCH (x:ExternalService {remote_id: 'pay'}) RETURN x.health") == [["ok"]]
    assert aw.xws["B"].sim_graphs() == []
    assert_green(aw)
