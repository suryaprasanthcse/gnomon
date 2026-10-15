"""Mutation suite MU22-MU23 (master §21.2, T-B rows): a swapped scorer library and a changed lambda are caught by K7."""

import pytest

from tests.support.exec_world import xw, xw_private  # noqa: F401
from tests.support.ir_world import ir, opened  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.mutation, pytest.mark.gate_tb]

EXPECTED = {"MU22": {"K7"}, "MU23": {"K7"}}


@pytest.fixture
def decided_copy(xw_private, redis_raw):
    xw = xw_private
    res = xw.executive().decide("i1", [], "fail over", "r1")
    assert res.outcome.code == "APPLIED"
    name = f"{xw.k.graph.name}:replay:mut-tb"
    redis_raw.execute_command("GRAPH.COPY", xw.k.graph.name, name)
    return name


@pytest.fixture
def restore_lambda():
    from gnomon.executive import scoring

    before = scoring.LAMBDA_D
    yield
    scoring.LAMBDA_D = before


def test_catalogue_adds_the_executive_mutations():
    from gnomon.audit.mutations import LAYER_MUTATIONS, MUTATIONS, TB_MUTATIONS

    assert list(TB_MUTATIONS) == ["MU22", "MU23"] and not set(TB_MUTATIONS) & (set(MUTATIONS) | set(LAYER_MUTATIONS))
    assert {mu: set(m.expected) for mu, m in TB_MUTATIONS.items()} == EXPECTED
    assert all(m.title for m in TB_MUTATIONS.values())


def test_mu22_a_different_library_under_the_same_name(db, redis_raw, decided_copy):
    from gnomon.audit.canonical import state_hash
    from gnomon.audit.mutations import apply_mutation
    from gnomon.audit.runner import audit

    assert audit(db, redis_raw, decided_copy, ["K7"]).ok
    before = state_hash(db.select_graph(decided_copy))
    apply_mutation(db, redis_raw, decided_copy, "MU22")
    assert state_hash(db.select_graph(decided_copy)) == before  # the graph is untouched: the library changed
    report = audit(db, redis_raw, decided_copy, ["K7"])
    assert EXPECTED["MU22"] <= set(report.failed)
    assert "library_sha" in [f["problem"] for f in report.results["K7"].findings]


def test_mu23_a_changed_lambda(db, redis_raw, decided_copy, restore_lambda):
    from gnomon.audit.mutations import apply_mutation
    from gnomon.audit.runner import audit

    assert audit(db, redis_raw, decided_copy, ["K7"]).ok
    apply_mutation(db, redis_raw, decided_copy, "MU23")
    report = audit(db, redis_raw, decided_copy, ["K7"])
    assert EXPECTED["MU23"] <= set(report.failed)
    assert {"config_mismatch", "resim_mismatch"} <= {f["problem"] for f in report.results["K7"].findings}


def test_the_mutations_refuse_a_graph_without_executive_decisions(db, redis_raw, golden_genesis, tenant):
    from gnomon.audit.mutations import MutationNotApplicable, apply_mutation

    redis_raw.execute_command("GRAPH.COPY", golden_genesis, tenant)
    for mu in EXPECTED:
        with pytest.raises(MutationNotApplicable):
            apply_mutation(db, redis_raw, tenant, mu)
