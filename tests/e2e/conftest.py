"""End-to-end fixtures: fresh genesis kernels, the incident world and one shared Core scenario run."""

import pytest

from tests.support.ir_world import ir, opened  # noqa: F401
from tests.support.query_world import qs  # noqa: F401
from tests.support.sim_world import fresh_kernel, kernels  # noqa: F401

CORE_SEED = 7


@pytest.fixture(scope="session")
def core_run(db, redis_raw, golden_genesis):
    """The Core scenario (seed 7, one investigator crash) run once with the policy model recording its responses."""
    from gnomon.agents.recorded import PolicyLLM, RecordingLLM
    from gnomon.sim.scenario import run_core
    from tests.conftest import tenant_scope

    store: dict = {}
    with tenant_scope(redis_raw) as key:
        kernel = fresh_kernel(db, redis_raw, golden_genesis, key)
        result = run_core(kernel, seed=CORE_SEED, llm=RecordingLLM(PolicyLLM(), store), crash=True)
        result.store = store
        yield result
from tests.support.exec_world import xw, xw_rollback  # noqa: E402,F401
