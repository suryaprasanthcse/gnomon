"""§11.4 synthetic company generator and infrastructure simulator."""

import pytest

from gnomon.audit.canonical import state_hash
from gnomon.sim.generator import apply_world, generate
from gnomon.sim.infra import ACTIONS, Infra
from tests.support.sim_world import EPOCH, Clock

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4b]


def test_generator_shape():
    w = generate(7)
    assert len(w.services) == 40 and len(w.teams) == 8
    index = {s["id"]: i for i, s in enumerate(w.services)}
    assert all(index[a] < index[b] for a, b, _ in w.edges), "edges must form a DAG"
    assert {hard for _, _, hard in w.edges} == {True, False}
    assert {s["criticality"] for s in w.services} <= {1, 2, 3, 4, 5} and len({s["criticality"] for s in w.services}) > 1
    changed = [o for o in w.ownership if o["supersede"]]
    assert changed and all(o["old_team"] and o["vf"] < w.load_pt for o in changed)  # backdated ownership changes
    assert sum(1 for d in w.deploys if d["bad"]) == 1
    assert sum(1 for r in w.replicas if not r["healthy"]) == 1
    bad = w.bad_deploy
    assert any(b == bad["service"] for _, b, hard in w.edges if hard), "the bad deploy has a hard dependent"


def test_generator_deterministic():
    assert generate(7) == generate(7)
    assert generate(7).digest() == generate(7).digest() != generate(8).digest()


def test_world_loads_to_the_same_canonical_hash(kernels):
    hashes = []
    for _ in range(2):
        k = kernels()
        report = apply_world(k, generate(7))
        assert report.proposals > 100 and report.rejected == []
        hashes.append(state_hash(k.graph))
    assert hashes[0] == hashes[1]
    k = kernels()
    apply_world(k, generate(8))
    assert state_hash(k.graph) != hashes[0]


def test_infra_actions_idempotent():
    w = generate(5, n_services=8, n_teams=3)
    infra = Infra(seed=5, world=w)
    svc = w.bad_deploy["service"]
    infra.start(EPOCH)
    first = infra.act("p1", "rollback", svc, now=EPOCH + 10)
    assert infra.act("p1", "rollback", svc, now=EPOCH + 20) == first
    assert infra.apply_count("p1") == 1
    assert infra.running_deploy(svc) != w.bad_deploy["id"]
    for action in ACTIONS:
        infra.act(f"x-{action}", action, svc, now=EPOCH + 30)
    with pytest.raises(ValueError):
        infra.act("bad", "nope", svc, now=EPOCH)


def test_infra_emits_observe_health():
    w = generate(5, n_services=8, n_teams=3)
    bad = w.bad_deploy["service"]
    a, b = Infra(seed=5, world=w), Infra(seed=5, world=w)
    for infra in (a, b):
        infra.start(EPOCH)
    assert a.health(bad) == "down"
    events = a.due_events(EPOCH + 10**7)
    assert events == b.due_events(EPOCH + 10**7)  # seeded latency: same events in the same order
    by_service = {e["service"]: e["health"] for e in events}
    assert by_service[bad] == "down"
    dependents = {x for x, y, hard in w.edges if y == bad and hard}
    assert dependents and all(by_service[d] == "degraded" for d in dependents)
    assert all(set(e) == {"source_id", "service", "health", "observed_pt"} for e in events)
    assert len({e["source_id"] for e in events}) == len(events)
    a.act("rb", "rollback", bad, now=EPOCH + 10**7)
    healed = {e["service"]: e["health"] for e in a.due_events(EPOCH + 2 * 10**7)}
    assert healed[bad] == "ok" and all(healed[d] == "ok" for d in dependents)
    assert a.due_events(EPOCH + 3 * 10**7) == []


def test_infra_alerts_the_bad_service_once():
    w = generate(5, n_services=8, n_teams=3)
    infra = Infra(seed=5, world=w)
    infra.start(EPOCH)
    (alert,) = infra.due_alerts(EPOCH + 10**7)
    assert alert["service"] == w.bad_deploy["service"] and alert["signature"] and alert["severity"] == "high"
    assert infra.due_alerts(EPOCH + 2 * 10**7) == []


def test_chaos_hooks():
    infra = Infra(seed=1)
    infra.drop_next(1)
    row = {"pid": "p", "kind": "post_update", "target": "status-page", "incident": "i", "payload": "x", "attempts": 0}
    assert infra.deliver(row)["outcome"] == "failed" and infra.apply_count("p") == 0
    assert infra.deliver(row)["outcome"] == "delivered" and infra.apply_count("p") == 1
    assert infra.deliver(row)["outcome"] == "delivered" and infra.apply_count("p") == 1
    assert Clock(1)() == 1
