"""Spike harness (A01): the FACTS.md registry and a private FalkorDB for spikes that need a quiet server.

Every spike test measures the real behaviour, asserts what it observed, and then checks that
.agent/FACTS.md records the same verdict (`facts.expect`). A changed image therefore fails the
spike instead of silently invalidating a platform fact later modules rely on.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import redis

ROOT = Path(__file__).resolve().parents[2]
FACTS_FILE = ROOT / ".agent" / "FACTS.md"
UDF_FIXTURES = ROOT / "tests" / "fixtures" / "udf"
FACT_LINE = re.compile(r"^(?P<id>[SF]\d+): (?P<verdict>PASS|FAIL) → (?P<text>.+)$")
IMAGE_LINE = re.compile(r"^falkordb_image: (?P<image>falkordb/falkordb@sha256:[0-9a-f]{64})$")


class Facts:
    def __init__(self, path: Path) -> None:
        self.image: str | None = None
        self.entries: dict[str, tuple[str, str]] = {}
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        if lines and (m := IMAGE_LINE.match(lines[0].strip())):
            self.image = m.group("image")
        for line in lines[1:]:
            if m := FACT_LINE.match(line.strip()):
                self.entries[m.group("id")] = (m.group("verdict"), m.group("text"))

    def expect(self, fact_id: str, observed_pass: bool) -> None:
        observed = "PASS" if observed_pass else "FAIL"
        assert fact_id in self.entries, f"{fact_id} is not recorded in .agent/FACTS.md (observed {observed})"
        recorded = self.entries[fact_id][0]
        assert recorded == observed, f"{fact_id}: FACTS.md records {recorded}, the image shows {observed}"


@pytest.fixture(scope="session")
def facts() -> Facts:
    return Facts(FACTS_FILE)


@dataclass
class PrivateServer:
    host: str
    port: int
    client: redis.Redis

    def graph(self, name: str | None = None):
        from falkordb import FalkorDB

        return FalkorDB(host=self.host, port=self.port).select_graph(name or f"spike-{uuid.uuid4().hex[:8]}")


@pytest.fixture(scope="session")
def private_falkordb(facts: Facts) -> Iterator[PrivateServer]:
    """A dedicated container (same pinned image) for timing, memory and server-config spikes."""
    from testcontainers.core.container import DockerContainer

    assert facts.image, "the image digest must head .agent/FACTS.md before spikes run"
    container = DockerContainer(facts.image).with_exposed_ports(6379)
    container.start()
    host, port = container.get_container_host_ip(), int(container.get_exposed_port(6379))
    client = redis.Redis(host=host, port=port, decode_responses=True)
    deadline = time.monotonic() + 60
    while True:
        try:
            if client.ping():
                break
        except redis.RedisError:
            if time.monotonic() > deadline:
                raise
        time.sleep(0.25)
    try:
        yield PrivateServer(host, port, client)
    finally:
        client.close()
        container.stop()


@pytest.fixture
def graph(db, tenant):
    return db.select_graph(tenant)


@pytest.fixture
def load_udf(redis_raw):
    """Loads tests/fixtures/udf/spike_<name>.js as library <Library>; deletes it after the test."""
    loaded: list[str] = []

    def _load(fixture: str, library: str) -> str:
        source = (UDF_FIXTURES / fixture).read_text(encoding="utf-8")
        redis_raw.execute_command("GRAPH.UDF", "LOAD", "REPLACE", library, source)
        loaded.append(library)
        return library

    yield _load
    for library in loaded:
        redis_raw.execute_command("GRAPH.UDF", "DELETE", library)


def _status_column(result) -> int:
    return [h[1] for h in result.header].index("status")


@pytest.fixture
def wait_operational():
    """wait_operational(graph) polls db.indexes() and db.constraints() until every row is OPERATIONAL."""

    def _wait(graph, timeout_s: float = 60.0) -> list[str]:
        seen: list[str] = []
        deadline = time.monotonic() + timeout_s
        while True:
            indexes = graph.query("CALL db.indexes()")
            constraints = graph.query("CALL db.constraints()")
            statuses = [row[_status_column(indexes)] for row in indexes.result_set]
            statuses += [row[_status_column(constraints)] for row in constraints.result_set]
            seen.extend(statuses)
            if all(s == "OPERATIONAL" for s in statuses):
                return seen
            assert time.monotonic() < deadline, f"not operational after {timeout_s}s: {statuses}"
            time.sleep(0.05)

    return _wait


@pytest.fixture
def build_log():
    """build_log(graph, n) writes n Rewrite-shaped nodes chained by CAUSED_BY (a log of n rewrites)."""

    def _build(graph, n: int) -> None:
        graph.query("CREATE INDEX FOR (r:Rewrite) ON (r.seq)")
        graph.query(
            "UNWIND range(1, $n) AS i CREATE (:Rewrite {id: 'pid-' + toString(i), seq: i, pt: 1791000000000 + i, "
            "rule: 'Noop', params: '{\"service\":\"svc-07\",\"note\":\"padding padding padding padding\"}', "
            "hash: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'})",
            {"n": n},
        )
        graph.query("MATCH (a:Rewrite) WHERE a.seq > 1 MATCH (b:Rewrite {seq: a.seq - 1}) CREATE (a)-[:CAUSED_BY]->(b)")

    return _build


def _error_of(fn) -> str | None:
    try:
        fn()
    except Exception as exc:  # spikes record platform errors as observations
        return str(exc)
    return None


@pytest.fixture
def error_of():
    """error_of(fn) runs fn and returns the error text, or None when it succeeds."""
    return _error_of
