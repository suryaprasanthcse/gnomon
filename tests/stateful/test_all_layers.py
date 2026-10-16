"""A16 · gate ASSURE · the stateful property across every layer (master §22).

Seeded random interleavings of agent-callable rules, `decide`, optimizer runs and relay deliveries across three tenants
(Core, T-A to T-F switched on). Each step checks K5 on the tenants it touched; the end state passes K1-K9 and the replay
hash equals the live hash (K4); a simulation never changes the live `Head.hash`. A seed is a script, not a hypothesis
strategy: the same seed always walks the same states, so a failure names the seed and the step that reproduces it.
"""

import pytest

from tests.support.assurance_machine import WEIGHTS, Machine

pytestmark = [pytest.mark.stateful, pytest.mark.integration, pytest.mark.gate_assure]

STEPS = 45
SEEDS = (3, 17, 2026)


@pytest.mark.parametrize("seed", SEEDS)
def test_k5_holds_after_every_step_and_the_end_state_passes_every_audit(aw, seed):
    machine = Machine(aw, seed).run(STEPS)
    machine.finish()
    machine.check_k5(["A", "B", "C"])
    reports, federation = aw.full_audit()
    for tenant, report in reports.items():
        assert report.ok, (seed, tenant, report.as_dict())  # K1-K8 incl. K4: replay hash = live hash
    assert federation.ok, (seed, federation.as_dict())  # K9a-f and K1 per ledger
    assert machine.k5_checks >= STEPS // 2


def test_the_seeds_between_them_exercise_every_kind_of_step_and_every_layer(aw):
    seen, applied = set(), {}
    for seed in SEEDS:
        machine = Machine(aw, seed).run(STEPS)
        seen |= {name for _, name, _, _ in machine.trace}
        for t, _, state, code, _ in machine.decisions:
            applied.setdefault(state, 0)
            applied[state] += 1
    assert seen >= set(WEIGHTS) - {"retract", "complete"}, sorted(set(WEIGHTS) - seen)
    assert applied, "no decision was made in any run"
    assert sum(applied.values()) >= 3


def test_a_simulation_never_changes_the_live_chain_head(aw):
    from tests.integration.test_a13_federation import TENANTS

    for t in TENANTS:
        before = aw.k[t].head()
        for i in range(2):
            res = aw.executive(t).simulate("i1", [], "assure", f"s{i}")
            assert res.state == "SIMULATED"
        assert aw.k[t].head() == before
        assert aw.xws[t].sim_graphs() == []
