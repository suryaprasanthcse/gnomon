"""GNOMON test harness (RUNBOOK_A §4.2): one real FalkorDB, tenant isolation, injected clock, hypothesis profiles.

Container sharing (patch P3): a single-process run starts its own Testcontainers FalkorDB in the
`falkordb_container` session fixture. Under pytest-xdist the controller process starts one container
before the workers spawn and hands its address to them through GNOMON_FALKORDB_URL, so every worker
shares it. That is safe because each test works in its own uniquely named graphs and UDF libraries are
identical across workers. A run with GNOMON_FALKORDB_URL already set uses that server and manages none.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
import redis
from hypothesis import settings

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parents[1]
UDF_DIR = ROOT / "udf"

# Placeholder until A01 pins falkordb/falkordb by digest (recorded in .agent/STATE.md).
FALKORDB_IMAGE = "falkordb/falkordb:latest"
FALKORDB_PORT = 6379
URL_ENV = "GNOMON_FALKORDB_URL"
PING_TIMEOUT_S = 60.0
CLOCK_EPOCH_MS = 1_791_000_000_000  # fixed start of the injected wall clock (2026-10-03T04:00:00Z)

settings.register_profile("ci", max_examples=200, derandomize=True, deadline=timedelta(seconds=2))
settings.register_profile("nightly", max_examples=2000, derandomize=False, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


@dataclass(frozen=True)
class FalkorEndpoint:
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"{self.host}:{self.port}"

    @classmethod
    def parse(cls, url: str) -> FalkorEndpoint:
        host, _, port = url.rpartition(":")
        return cls(host, int(port))


def _wait_for_ping(endpoint: FalkorEndpoint, timeout_s: float = PING_TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        client = redis.Redis(host=endpoint.host, port=endpoint.port, socket_timeout=2)
        try:
            if client.ping():
                return
        except redis.RedisError as exc:
            last_error = exc
        finally:
            client.close()
        time.sleep(0.25)
    raise RuntimeError(f"FalkorDB at {endpoint.url} did not answer PING within {timeout_s:.0f}s: {last_error!r}")


class FalkorContainer:
    """The pinned FalkorDB image under Testcontainers (contract C5: never mocked)."""

    def __init__(self, image: str = FALKORDB_IMAGE) -> None:
        from testcontainers.core.container import DockerContainer

        self._container = DockerContainer(image).with_exposed_ports(FALKORDB_PORT)

    def start(self) -> FalkorEndpoint:
        self._container.start()
        endpoint = FalkorEndpoint(
            self._container.get_container_host_ip(),
            int(self._container.get_exposed_port(FALKORDB_PORT)),
        )
        _wait_for_ping(endpoint)
        return endpoint

    def stop(self) -> None:
        self._container.stop()


_SHARED_CONTAINER = pytest.StashKey[FalkorContainer]()


def _xdist_workers_requested(config: pytest.Config) -> bool:
    if getattr(config.option, "dist", "no") == "no":
        return False
    workers = getattr(config.option, "numprocesses", None)
    return bool(workers)


def pytest_configure(config: pytest.Config) -> None:
    is_worker = hasattr(config, "workerinput")
    if is_worker or os.environ.get(URL_ENV) or not _xdist_workers_requested(config):
        return
    container = FalkorContainer()
    endpoint = container.start()
    config.stash[_SHARED_CONTAINER] = container
    os.environ[URL_ENV] = endpoint.url  # inherited by the worker processes spawned after configure


def pytest_unconfigure(config: pytest.Config) -> None:
    container = config.stash.get(_SHARED_CONTAINER, None)
    if container is not None:
        os.environ.pop(URL_ENV, None)
        container.stop()


@pytest.fixture(scope="session")
def falkordb_container() -> Iterator[FalkorEndpoint]:
    url = os.environ.get(URL_ENV)
    if url:
        endpoint = FalkorEndpoint.parse(url)
        _wait_for_ping(endpoint)
        yield endpoint
        return
    container = FalkorContainer()
    endpoint = container.start()
    try:
        yield endpoint
    finally:
        container.stop()


@pytest.fixture(scope="session")
def redis_raw(falkordb_container: FalkorEndpoint) -> Iterator[redis.Redis]:
    """Raw client for GRAPH.UDF, GRAPH.CONSTRAINT, GRAPH.COPY, GRAPH.LIST and MEMORY USAGE."""
    client = redis.Redis(host=falkordb_container.host, port=falkordb_container.port, decode_responses=True)
    yield client
    client.close()


@pytest.fixture(scope="session")
def db(falkordb_container: FalkorEndpoint):
    from falkordb import FalkorDB

    client = FalkorDB(host=falkordb_container.host, port=falkordb_container.port)
    yield client
    client.connection.close()


@pytest.fixture(scope="session")
def udf_loaded(redis_raw: redis.Redis) -> dict[str, str]:
    """Loads every library in udf/ (kernel.js → Kernel) with LOAD REPLACE; returns library → source sha256."""
    shas: dict[str, str] = {}
    for path in sorted(UDF_DIR.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        library = path.stem[:1].upper() + path.stem[1:]
        redis_raw.execute_command("GRAPH.UDF", "LOAD", "REPLACE", library, source)
        shas[library] = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return shas


def _delete_tenant_graphs(client: redis.Redis, key: str) -> None:
    for name in client.execute_command("GRAPH.LIST"):
        if name == key or name.startswith(key + ":"):
            client.execute_command("GRAPH.DELETE", name)


@contextmanager
def tenant_scope(client: redis.Redis) -> Iterator[str]:
    """A unique live key t:test-<uuid8>; on exit it and every t:test-<uuid8>:* scratch graph are deleted."""
    key = f"t:test-{uuid.uuid4().hex[:8]}"
    try:
        yield key
    finally:
        _delete_tenant_graphs(client, key)


@pytest.fixture
def tenant(redis_raw: redis.Redis) -> Iterator[str]:
    with tenant_scope(redis_raw) as key:
        yield key


@pytest.fixture
def genesis():
    raise RuntimeError(
        "the genesis fixture is installed by module A03 (KernelHandle on a GRAPH.COPY of golden_genesis, "
        "patch P3); it is not available at gate H0"
    )


class SteppableClock:
    """Injected wall clock in epoch milliseconds (contract C8). Reading never advances it."""

    def __init__(self, start_ms: int) -> None:
        self._now_ms = start_ms

    def __call__(self) -> int:
        return self._now_ms

    def step(self, ms: int) -> int:
        if ms < 0:
            raise ValueError(f"the clock only moves forward; got step({ms})")
        self._now_ms += ms
        return self._now_ms


@pytest.fixture
def clock() -> SteppableClock:
    return SteppableClock(CLOCK_EPOCH_MS)


@pytest.fixture
def recorded_llm():
    raise RuntimeError(
        "the recorded_llm fixture is installed by module A09 (responses replayed from tests/fixtures/llm/ "
        "by prompt hash); it is not available at gate H0"
    )
