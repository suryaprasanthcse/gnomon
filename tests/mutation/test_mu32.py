"""A16 · gate ASSURE · MU32 and the complete mutation suite MU1-MU32 (master §21.2).

MU32 is the race the lints refuse: ClaimTask_v0 has no lease guard, L1 refuses it at registration, and forced onto a
scratch copy 50 claims on one task all succeed, which K5a reports as 50 open leases. The suite test then runs every
mutation of the catalogue in the world that holds what it corrupts and prints which checks fired for each.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.mutation, pytest.mark.gate_assure]

# master §21.2: mutation -> checks that must detect it
SPEC = {
    "MU1": {"K1", "K4"}, "MU2": {"K1"}, "MU3": {"K1"}, "MU4": {"K2", "K1"}, "MU5": {"K1"}, "MU6": {"K3"},
    "MU7": {"K3", "K4"}, "MU8": {"K3", "K4"}, "MU9": {"K4"}, "MU10": {"K4", "K5b", "K5d"}, "MU11": {"K4"},
    "MU12": {"K4"}, "MU13": {"K5a", "K4"}, "MU14": {"K5b", "K4"}, "MU15": {"K5c", "K4"}, "MU16": {"K5d", "K4"},
    "MU17": {"K6", "K4"}, "MU18": {"K6", "K2"}, "MU19": {"K7", "K4"}, "MU20": {"K7", "K4"}, "MU21": {"K7", "K4"},
    "MU22": {"K7"}, "MU23": {"K7"}, "MU24": {"K8"}, "MU25": {"K8"}, "MU26": {"K9b", "K9c", "K4"}, "MU27": {"K9b", "K9c"},
    "MU28": {"K9e", "K4"}, "MU29": {"K9d", "K4"}, "MU30": {"K9a", "K4"}, "MU31": {"K9f", "K4"}, "MU32": {"K5a"},
}
FEDERATION_VICTIM = {"MU26": "B", "MU27": "B", "MU28": "B", "MU29": "A", "MU30": "A", "MU31": "B"}


def test_the_catalogue_is_mu1_to_mu32_in_order_with_the_expected_checks_of_the_spec():
    from gnomon.audit.mutations import all_mutations

    catalogue = all_mutations()
    assert list(catalogue) == [f"MU{n}" for n in range(1, 33)]
    assert {mu: set(m.expected) for mu, m in catalogue.items()} == SPEC
    assert all(m.title for m in catalogue.values())


def test_mu32_is_refused_at_registration_by_lint_l1(opened):
    from gnomon.footprint.lints import lint
    from gnomon.kernel import macros
    from gnomon.kernel.registry import RuleDef, register_rule_params

    from gnomon.audit.mutations import CLAIM_V0_BODY

    v0 = RuleDef(name="ClaimTask_v0", version=1, cls="memory", layer="core", caller=("investigator", "triage"),
                 prelude="P0", body=macros.expand(CLAIM_V0_BODY), reads=("Agent", "Agent.id", "Task", "Task.id"),
                 writes=("HOLDS", "Task.state"), key_templates=("HOLDS:*:{p.task}", "Task:{p.task}:state", "Task:*"))
    assert [r.id for r in lint(v0).results if not r.ok] == ["L1"]
    refused = opened.k.submit("RegisterRule", register_rule_params(v0), pid="reg-v0", agent="ops-1")
    assert refused.code == "SCHEMA_REJECTED" and refused.detail["reason"] == "lint failed"


def test_mu32_forced_onto_a_scratch_copy_trips_k5a_and_leaves_the_live_graph_clean(opened, db, redis_raw):
    from gnomon.audit.runner import audit
    from tests.support.mutation_suite import run_on_copy

    live = opened.k.graph.name
    result = run_on_copy(db, redis_raw, live, "MU32", "core", checks=["K5"])
    assert result.caught and result.fired == ["K5a"], result
    assert audit(db, redis_raw, live, ["K5"]).ok
    assert opened.k.read("MATCH (:Agent)-[l:HOLDS]->(:Task {id: 't1'}) RETURN count(l)") == [[0]]
    assert [g for g in redis_raw.execute_command("GRAPH.LIST") if ":replay:mut-" in g and g.startswith(live)] == []


def test_mu32_shows_fifty_open_leases(opened, db, redis_raw):
    from gnomon.audit.mutations import apply_mutation
    from gnomon.audit.runner import audit

    key = f"{opened.k.graph.name}:replay:mut-32"
    redis_raw.execute_command("GRAPH.COPY", opened.k.graph.name, key)
    try:
        apply_mutation(db, redis_raw, key, "MU32")
        finding = audit(db, redis_raw, key, ["K5"]).results["K5a"].findings[0]
        assert finding["task"] == "t1" and finding["open_leases"] == 50
    finally:
        redis_raw.execute_command("GRAPH.DELETE", key)


def test_mu32_refuses_a_graph_with_no_open_task(db, redis_raw, golden_genesis, tenant):
    from gnomon.audit.mutations import MutationNotApplicable, apply_mutation

    redis_raw.execute_command("GRAPH.COPY", golden_genesis, tenant)
    with pytest.raises(MutationNotApplicable):
        apply_mutation(db, redis_raw, tenant, "MU32")


@pytest.mark.timeout(1500)  # five worlds are built in one test (setup alone is minutes under -n 4); the default is 300 s
def test_the_complete_suite_mu1_to_mu32_runs_and_every_mutation_is_caught(
        request, db, redis_raw, scenario, xw_private, make_ow, aw):
    from gnomon.executive import scoring
    from tests.integration.test_a13_federation import _busy
    from tests.support.mutation_suite import Suite, run_federated, run_on_copy

    suite = Suite()

    # MU1-MU18: the Core audit scenario (claims, a completed task, a cited update, a voided fact, a cause).
    for n in range(1, 19):
        suite.add(run_on_copy(db, redis_raw, scenario.key, f"MU{n}", "core"))

    # MU19-MU23 and MU32: a tenant with Executive decisions (a private scorer library, so MU22 disturbs nobody).
    decided = xw_private
    assert decided.executive().decide("i1", [], "fail over", "r1").outcome.code == "APPLIED"
    live = decided.k.graph.name
    lam = scoring.LAMBDA_D
    for n in (19, 20, 21, 22):
        suite.add(run_on_copy(db, redis_raw, live, f"MU{n}", "decisions"))
    suite.add(run_on_copy(db, redis_raw, live, "MU23", "decisions", restore=lambda: setattr(scoring, "LAMBDA_D", lam)))
    suite.add(run_on_copy(db, redis_raw, live, "MU32", "decisions", checks=["K5"]))

    # MU24-MU25: a tenant whose optimizer built an index.
    ow = make_ow()
    ow.build_index("edge", "OWNED_BY", "vt")
    for n in (24, 25):
        suite.add(run_on_copy(db, redis_raw, ow.key, f"MU{n}", "optimizer", checks=["K8"], settle=0.3))

    # MU26-MU31: a busy federation (health published, notices sent and delivered, one message left queued).
    relay = aw.relay()
    aw.consume("B", "A", "pay-db", "checkout")
    aw.consume("C", "A", "pay-db", "billing")
    relay.deliver("B", "A")
    relay.deliver("C", "A")
    _busy((aw, relay))
    for mu, victim in FEDERATION_VICTIM.items():
        suite.add(run_federated(aw, mu, victim))

    for line in suite.table():
        print(line)
    request.node.user_properties.extend((f"{mu}_fired", ",".join(r.fired)) for mu, r in suite.results.items())
    assert sorted(suite.results, key=lambda mu: int(mu[2:])) == [f"MU{n}" for n in range(1, 33)]
    assert suite.missed() == [], suite.table()
    assert {mu: set(r.expected) for mu, r in suite.results.items()} == SPEC
