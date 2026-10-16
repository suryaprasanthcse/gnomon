"""A16 · gate ASSURE · determinism (master §22 "End-to-end", contract C8): three runs of one seed are one run.

Three fresh copies of the same starting world walk the same seeded script (rules, decisions, optimizer runs, relay
deliveries). They must end with identical state hashes, chain heads, decision shas, step traces and federated logs; a
different seed must not.
"""

import pytest

from tests.support.assurance_machine import Machine, snapshot

pytestmark = [pytest.mark.stateful, pytest.mark.integration, pytest.mark.gate_assure]

STEPS = 30


def run(world, seed):
    machine = Machine(world, seed).run(STEPS)
    machine.finish()
    return snapshot(world, machine)


def test_three_seeded_runs_produce_identical_hashes_decision_shas_and_federated_logs(make_aw):
    runs = [run(make_aw(), 11) for _ in range(3)]
    first = runs[0]
    assert first["decisions"], "the script makes no decision, so decision shas are not compared"
    assert any(sha for *_, sha in first["decisions"])
    assert first["fedlog"] and any(rule == "ReceiveMessage.notice" or rule.startswith("ReceiveMessage")
                                   for *_, rule, _ in first["fedlog"])
    for other in runs[1:]:
        assert other["state"] == first["state"]  # canonical state hash of each tenant
        assert other["heads"] == first["heads"]  # the chain heads
        assert other["decisions"] == first["decisions"]  # decision ids, outcomes and envelope shas
        assert other["trace"] == first["trace"]
        assert other["fedlog"] == first["fedlog"]


def test_a_different_seed_walks_a_different_history(make_aw):
    one, two = run(make_aw(), 11), run(make_aw(), 12)
    assert one["trace"] != two["trace"] and one["state"] != two["state"]
