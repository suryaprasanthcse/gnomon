"""A14 · gate TE · Federated blast radius (master §18, §15.5-§15.8, §14.1, §26 row E).

Against the real FalkorDB (contract C5). The world is the Executive world of A11 with T-D switched on and the scorer
registered as version 2 (tests/support/fed_blast_world.py); two other tenants subscribe to the provider's service c
through the real X3 rule. Every integer below is derived by hand in that module's docstring.
"""

from __future__ import annotations

import json

import pytest

from tests.support.fed_blast_world import build_fed, open_channel, subscribe

pytestmark = [pytest.mark.integration, pytest.mark.gate_te]

V1_WEIGHTS = {"svc:c": 5040, "svc:b": 3780, "svc:a": 2520, "svc:f": 5040}
CONSUMER_WEIGHTS = {"cons:acme/checkout": 5040, "cons:globex/login": 6300}


def candidate(res, rule, **match):
    return next(c for c in res.candidates if c["rule"] == rule and all(c["params"].get(k) == v for k, v in match.items()))


def weights(out):
    return {row[0]: row[3] for row in out["impacted"]}


def score(xw, federation):
    from gnomon.executive import engine

    return engine.score_graph(xw.k.graph, "i1", 4, xw.library, federation)


def test_v2_counts_consumer_harm_so_the_provider_prefers_failover_to_isolation(fxw):
    res = fxw.executive(federation=True).simulate("i1", [], "", "fed")
    assert res.state == "SIMULATED", res.error
    isolate = candidate(res, "IsolateService", svc="c")
    failover = candidate(res, "FailoverService", svc="c", replica="c-r1")
    # Isolating c takes both hard consumers down: their weights double (5040 -> 10080, 6300 -> 12600).
    assert (isolate["benefit"], isolate["harm"], isolate["score_num"]) == (5040, 11340 + 5040 + 6300, -57960)
    # Failing over rescues the local dependents and the consumers: 16380 + 5040 + 6300.
    assert (failover["benefit"], failover["harm"], failover["score_num"]) == (27720, 0, 55440)
    assert candidate(res, "FailoverService", svc="c", replica="c-r2")["outcome"] == "guard_pruned"
    assert res.winner["rule"] == "FailoverService" and res.winner["params"]["replica"] == "c-r1"
    assert [c["rule"] for c in res.candidates if c["won"]] == ["FailoverService"]
    assert weights(res.base_output) == {**V1_WEIGHTS, **CONSUMER_WEIGHTS}


def test_the_same_world_scored_without_federation_cannot_see_the_consumers(fxw):
    res = fxw.executive(federation=False).simulate("i1", [], "", "v1")
    isolate = candidate(res, "IsolateService", svc="c")
    failover = candidate(res, "FailoverService", svc="c", replica="c-r1")
    assert (isolate["benefit"], isolate["harm"], isolate["score_num"]) == (5040, 11340, -23940)
    assert (failover["benefit"], failover["harm"], failover["score_num"]) == (16380, 0, 32760)
    assert weights(res.base_output) == V1_WEIGHTS


def test_v1_unchanged_when_federation_false(opened, db, redis_raw):
    w = build_fed(opened, db, redis_raw, consumers=())
    before_v1 = score(w, False)
    before_v2 = score(w, True)
    assert before_v1["version"] == 1 and weights(before_v1) == V1_WEIGHTS
    assert before_v2 == {**before_v1, "version": 2}  # no consumers yet: v2 differs from v1 only by its version
    for tenant, svc, criticality, hard in (("acme", "checkout", 4, True), ("globex", "login", 5, True)):
        open_channel(w, tenant)
        assert subscribe(w, tenant, svc, "c", criticality=criticality, hard=hard).code == "APPLIED"
    after_v1 = score(w, False)
    assert json.dumps(after_v1, sort_keys=True) == json.dumps(before_v1, sort_keys=True)  # bit-identical
    assert not any(u.startswith("cons:") for u in after_v1["region"])
    after_v2 = score(w, True)
    assert after_v2["version"] == 2
    assert weights(after_v2) == {**V1_WEIGHTS, **CONSUMER_WEIGHTS}
    assert {"cons:acme/checkout", "cons:globex/login"} <= set(after_v2["region"])
    assert not {"cons:acme/checkout", "cons:globex/login"} & set(after_v2["sources"])  # a consumer is never a source


def test_consumers_are_leaves_and_a_soft_consumer_degrades_instead_of_failing(opened, db, redis_raw):
    w = build_fed(opened, db, redis_raw, consumers=(("acme", "checkout", 4, False),))
    out = score(w, True)
    rows = {r[0]: r for r in out["impacted"]}
    # c is degraded; a soft consumer takes degraded softly: 4 * 1 * 1260, one level from the source, parent c.
    assert rows["cons:acme/checkout"][1:4] == ["degraded", 1, 5040] and rows["cons:acme/checkout"][4] == "svc:c"
    assert not any(e[0].startswith("cons:") for e in out["owner_edges"])  # a consumer has no owner of its own


def test_consumer_side_sources_an_unhealthy_proxy_drives_local_impact(opened, db, redis_raw):
    w = build_fed(opened, db, redis_raw, consumers=(), c_health="ok")
    ir = w.ir
    ir.ok("RegisterEntity.ExternalService", {"id": "prov.auth", "tenant": "prov", "remote_id": "auth",
                                             "contract_criticality": 3}, "ops-1")
    ir.ok("AssertFact.DEPENDS_ON", {"s": "svc:c", "o": "ext:prov.auth", "vf": ir.clock, "supersede": False, "hard": True},
          "ops-1")
    assert score(w, True)["sources"] == []  # the proxy is healthy
    ir.poke("MATCH (x:ExternalService {id: 'prov.auth'}) SET x.health = 'down'")
    v2 = score(w, True)
    assert v2["sources"] == ["ext:prov.auth"]
    rows = {r[0]: r for r in v2["impacted"]}
    assert rows["ext:prov.auth"][1:4] == ["down", 0, 3 * 2 * 2520]  # contract_criticality 3, down, W
    assert rows["svc:c"][1:5] == ["down", 1, 2 * 2 * 1260, "ext:prov.auth"]  # the hard dependent fails with it
    assert rows["svc:b"][1] == "down" and rows["svc:a"][1] == "down"
    assert weights(score(w, False))["ext:prov.auth"] == 0  # v1 weighs a proxy by `criticality`, which it has none of


def test_subscription_mid_decision_goes_stale(fxw):
    landed = []

    def late_subscription(executive, d):
        if not landed:
            landed.append(d.attempt)
            open_channel(fxw, "initech")
            assert subscribe(fxw, "initech", "ops", "c", criticality=5).code == "APPLIED"

    res = fxw.executive(federation=True, before_commit=late_subscription).decide("i1", [], "fail over", "r1")
    assert landed == [1]
    assert res.stale_flushes == 1 and res.attempt == 2, res.trace
    assert "FLUSH" in res.trace and res.outcome.code == "APPLIED"
    assert "cons:initech/ops" in res.base_output["region"]  # the re-simulation saw the new consumer
    assert res.envelope["channels"] == {"acme": 2, "globex": 2, "initech": 2}
    assert fxw.sim_graphs() == []


def test_a_v1_decision_does_not_notice_a_subscription(fxw):
    landed = []

    def late_subscription(executive, d):
        if not landed:
            landed.append(d.attempt)
            open_channel(fxw, "initech")
            subscribe(fxw, "initech", "ops", "c", criticality=5)

    res = fxw.executive(federation=False, before_commit=late_subscription).decide("i1", [], "fail over", "r1")
    assert res.flushes == 0 and res.attempt == 1 and res.outcome.code == "APPLIED", res.trace
    assert res.envelope["channels"] == {}


def test_the_federated_footprint_holds_the_consumer_channel_and_subscription_keys(fxw):
    from gnomon.executive import scoring

    res = fxw.executive(federation=True).simulate("i1", [], "", "fp")
    keys = set(scoring.footprint(res.base_output, "i1", True))
    assert {"CONSUMES:cons:acme/checkout:*", "Channel:in:acme", "CONSUMES:cons:globex/login:*", "Channel:in:globex",
            "CONSUMES:*:svc:c", "CONSUMES:*:svc:a", "CONSUMES:*:svc:b", "CONSUMES:*:svc:f"} <= keys
    plain = set(scoring.footprint(res.base_output, "i1"))
    assert not any(k.startswith(("CONSUMES", "Channel:in")) for k in plain)
    assert plain <= keys


def test_proxy_keys_join_the_footprint_when_a_proxy_is_in_the_region():
    from gnomon.executive import scoring

    base = {"region": ["svc:c", "ext:prov.auth"]}
    assert {"ExternalService:prov.auth:state", "DEPENDS_ON:*:ext:prov.auth"} <= set(scoring.footprint(base, "i1", True))
    assert not any(k.startswith("ExternalService") for k in scoring.footprint(base, "i1"))


def test_a_proxy_is_cited_by_the_remote_id_that_x3_health_writes():
    from gnomon.executive import scoring

    base = {"region": ["svc:a", "ext:prov.auth"]}
    keys = set(scoring.footprint(base, "i1", True, {"ext:prov.auth": "auth"}))
    assert {"ExternalService:prov.auth:state", "ExternalService:auth:state"} <= keys  # the id and the remote id


def test_envelope_channels_recorded_and_k7_rebuilds_them(fxw, db, redis_raw):
    from gnomon.audit.runner import audit

    res = fxw.executive(federation=True).decide("i1", [], "fail over", "r1")
    assert res.outcome.code == "APPLIED"
    assert res.envelope["channels"] == {"acme": 2, "globex": 2}  # one subscription each: next_seq 1 -> 2
    assert res.winner["rule"] == "FailoverService"
    view = fxw.k.read("MATCH (r:Rewrite {id: $pid}) RETURN r.view_keys", {"pid": res.pid})[0][0]
    assert {"CONSUMES:cons:acme/checkout:*", "Channel:in:globex", "CONSUMES:*:svc:c"} <= set(view)
    report = audit(db, redis_raw, fxw.k.graph.name, ["K7"])
    assert report.ok, report.results["K7"].findings
    assert fxw.sim_graphs() == []


def test_k7_names_a_decision_whose_recorded_channel_positions_no_longer_match(fxw, db, redis_raw):
    from gnomon.audit import k7_resim

    res = fxw.executive(federation=True).decide("i1", [], "fail over", "r1")
    assert res.outcome.code == "APPLIED"
    # K7 compares the recorded positions with the ones the rebuilt base has; a federated scorer it cannot rebuild
    # them for (here: pretending the scorer was version 1) is a named mismatch, not a silent pass.
    fxw.k.graph.query("MATCH (s:Scorer) SET s.version = 1")
    found = [f["problem"] for f in k7_resim.resimulate(fxw.k.graph, db, redis_raw)]
    assert "channels_mismatch" in found


def test_a_federated_executive_refuses_a_scorer_that_is_not_version_2(opened, db, redis_raw):
    w = build_fed(opened, db, redis_raw, scorer_version=1, consumers=())
    res = w.executive(federation=True).decide("i1", [], "fail over", "r1")
    assert res.state == "FAILED" and "version 2" in res.error
    assert w.sim_graphs() == []
