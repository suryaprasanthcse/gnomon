"""A09 test world: fresh genesis kernels for the scenario runs and a manual clock for the ops loops."""

from __future__ import annotations

import uuid
from contextlib import ExitStack

import pytest

EPOCH = 1_791_000_000_000


class Clock:
    """Wall clock in epoch ms for the dispatcher and scheduler (contract C8): reading never advances it."""

    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def step(self, ms: int) -> int:
        self.now += ms
        return self.now


def fresh_kernel(db, redis_raw, golden_genesis: str, key: str):
    from gnomon.kernel.service import KernelHandle
    from tests.conftest import kernel_test_registry

    redis_raw.execute_command("GRAPH.COPY", golden_genesis, key)
    return KernelHandle(db.select_graph(key), kernel_test_registry())


@pytest.fixture
def kernels(db, redis_raw, golden_genesis):
    """A factory of fresh genesis kernels (GRAPH.COPY of the golden genesis); every graph is deleted afterwards."""
    from tests.conftest import tenant_scope

    with ExitStack() as stack:
        def make():
            key = stack.enter_context(tenant_scope(redis_raw))
            return fresh_kernel(db, redis_raw, golden_genesis, key)

        yield make


def new_key() -> str:
    return f"t:test-{uuid.uuid4().hex[:8]}"
