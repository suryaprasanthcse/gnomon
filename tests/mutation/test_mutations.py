"""Mutation suite MU1–MU18 (master §21.2): each corruption, applied to a scratch copy, is caught by its listed checks.

Other checks may also fire; the listed ones must. A mutation caught by none fails CI.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.mutation, pytest.mark.gate_g3]

# §21.2, Core rows.
EXPECTED = {
    "MU1": {"K1", "K4"}, "MU2": {"K1"}, "MU3": {"K1"}, "MU4": {"K2", "K1"}, "MU5": {"K1"}, "MU6": {"K3"},
    "MU7": {"K3", "K4"}, "MU8": {"K3", "K4"}, "MU9": {"K4"}, "MU10": {"K4", "K5b", "K5d"}, "MU11": {"K4"},
    "MU12": {"K4"}, "MU13": {"K5a", "K4"}, "MU14": {"K5b", "K4"}, "MU15": {"K5c", "K4"}, "MU16": {"K5d", "K4"},
    "MU17": {"K6", "K4"}, "MU18": {"K6", "K2"},
}


def test_catalogue_matches_the_spec():
    from gnomon.audit.mutations import MUTATIONS

    assert list(MUTATIONS) == [f"MU{n}" for n in range(1, 19)]
    assert {mu: set(m.expected) for mu, m in MUTATIONS.items()} == EXPECTED
    assert all(m.title for m in MUTATIONS.values())


def test_clean_copy_passes_every_check(db, redis_raw, scratch):
    from gnomon.audit.runner import audit

    report = audit(db, redis_raw, scratch)
    assert report.ok, report.as_dict()


@pytest.mark.parametrize("mu", list(EXPECTED))
def test_mutation_is_detected(db, redis_raw, scratch, mu):
    from gnomon.audit.canonical import state_hash
    from gnomon.audit.mutations import apply_mutation
    from gnomon.audit.runner import audit

    before = state_hash(db.select_graph(scratch))
    apply_mutation(db, redis_raw, scratch, mu)
    assert state_hash(db.select_graph(scratch)) != before, f"{mu} changed nothing"
    report = audit(db, redis_raw, scratch)
    assert EXPECTED[mu] <= set(report.failed), (mu, report.as_dict())


def test_mutations_refuse_a_graph_without_targets(db, redis_raw, golden_genesis, tenant):
    from gnomon.audit.mutations import MutationNotApplicable, apply_mutation

    redis_raw.execute_command("GRAPH.COPY", golden_genesis, tenant)
    with pytest.raises(MutationNotApplicable):
        apply_mutation(db, redis_raw, tenant, "MU13")
    with pytest.raises(KeyError):
        apply_mutation(db, redis_raw, tenant, "MU99")
