"""Mutation suite MU19-MU21 (master §21.2, T-A rows): corruptions of decisions are caught by K7 and K4."""

import pytest

from tests.support.decision_world import activate
from tests.support.ir_world import ir, opened  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.mutation, pytest.mark.gate_ta]

EXPECTED = {"MU19": {"K7", "K4"}, "MU20": {"K7", "K4"}, "MU21": {"K7", "K4"}}


@pytest.fixture
def decided_copy(opened, redis_raw):
    dw = activate(opened)
    assert dw.commit(dw.envelope("D-1")).code == "APPLIED"
    d2 = dw.approved("D-2")
    assert dw.commit(d2, rule="NoopActionGated", params=dw.params(d2, {"mark": "m2"})).code == "APPLIED"
    name = f"{opened.k.graph.name}:replay:mut-ta"
    redis_raw.execute_command("GRAPH.COPY", opened.k.graph.name, name)
    return name


def test_catalogue_adds_the_decision_mutations_after_the_core_ones():
    from gnomon.audit.mutations import LAYER_MUTATIONS, MUTATIONS

    assert list(MUTATIONS) == [f"MU{n}" for n in range(1, 19)]  # unchanged
    assert list(LAYER_MUTATIONS) == list(EXPECTED)
    assert {mu: set(m.expected) for mu, m in LAYER_MUTATIONS.items()} == EXPECTED
    assert all(m.title for m in LAYER_MUTATIONS.values())


def test_clean_decided_copy_passes(db, redis_raw, decided_copy):
    from gnomon.audit.runner import audit

    assert audit(db, redis_raw, decided_copy, ["K1", "K2", "K3", "K4", "K5", "K6", "K7"]).ok


@pytest.mark.parametrize("mu", list(EXPECTED))
def test_mutation_is_detected(db, redis_raw, decided_copy, mu):
    from gnomon.audit.canonical import state_hash
    from gnomon.audit.mutations import apply_mutation
    from gnomon.audit.runner import audit

    before = state_hash(db.select_graph(decided_copy))
    apply_mutation(db, redis_raw, decided_copy, mu)
    assert state_hash(db.select_graph(decided_copy)) != before, f"{mu} changed nothing"
    report = audit(db, redis_raw, decided_copy, ["K1", "K2", "K3", "K4", "K5", "K6", "K7"])
    assert EXPECTED[mu] <= set(report.failed), (mu, report.as_dict())


def test_mutations_refuse_a_graph_without_decisions(db, redis_raw, golden_genesis, tenant):
    from gnomon.audit.mutations import MutationNotApplicable, apply_mutation

    redis_raw.execute_command("GRAPH.COPY", golden_genesis, tenant)
    for mu in EXPECTED:
        with pytest.raises(MutationNotApplicable):
            apply_mutation(db, redis_raw, tenant, mu)
