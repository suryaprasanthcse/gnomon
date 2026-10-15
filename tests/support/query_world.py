"""Query-service test world (A08; imported by the conftests of tests/integration/query and tests/integration/mcp).

`qs` is a QueryService over the A06 incident world (`opened`: incident i1 on service a with task t1, skill ops),
in replay mode on a clock that advances 10 ms per proposal, so leases and Head.pt are deterministic (contract C8).
`topology` adds services c-f and DEPENDS_ON facts a→b→c→d, e→c (f isolated) for the algorithm tools.
"""

from __future__ import annotations

import pytest

from tests.support.ir_world import AGENTS, IR

INF = 9007199254740991
SERVICES = ("c", "d", "e", "f")
EDGES = (("a", "b"), ("b", "c"), ("c", "d"), ("e", "c"))


def replay_clock(ir: IR):
    """A clock callable for QueryService(mode="replay"): each call moves the world's clock 10 ms forward."""

    def tick() -> int:
        ir.clock += 10
        return ir.clock

    return tick


def register_services(ir: IR, ids=SERVICES) -> None:
    for sid in ids:
        ir.ok("RegisterEntity.Service", {"id": sid, "name": sid, "tier": "t1", "criticality": 3, "user_facing": True},
              "ops-1")


@pytest.fixture
def qs(opened):
    from gnomon.query.service import QueryService

    return QueryService(opened.k, mode="replay", clock=replay_clock(opened))


@pytest.fixture
def topology(opened):
    ir = opened
    register_services(ir)
    for s, o in EDGES:
        ir.ok("AssertFact.DEPENDS_ON", {"s": f"svc:{s}", "o": f"svc:{o}", "vf": ir.clock, "supersede": False,
                                         "hard": True}, "ops-1")
    return ir


def make_world(db, redis_raw, golden_genesis: str, key: str) -> IR:
    """The A06 `ir` fixture without pytest: a GRAPH.COPY of the golden genesis plus agents, services, owner, deploy."""
    from gnomon.kernel.service import KernelHandle
    from tests.conftest import kernel_test_registry

    redis_raw.execute_command("GRAPH.COPY", golden_genesis, key)
    w = IR(KernelHandle(db.select_graph(key), kernel_test_registry()), redis_raw)
    for aid, role, skills in AGENTS:
        w.ok("RegisterAgent", {"op": "register", "id": aid, "role": role, "skills": skills}, "ops-1")
    for sid in ("a", "b", "c"):
        w.ok("RegisterEntity.Service", {"id": sid, "name": sid, "tier": "t1", "criticality": 3, "user_facing": True},
             "ops-1")
    w.ok("RegisterEntity.Team", {"id": "t1", "name": "t1"}, "ops-1")
    w.ok("RegisterEntity.Deploy", {"id": "d1", "service": "a", "version": "1", "verified": True,
                                   "expected_health": "ok"}, "ops-1")
    w.ok("AssertFact.OWNED_BY", {"s": "svc:a", "o": "team:t1", "vf": w.clock, "supersede": False}, "ops-1")
    w.ok("AssertFact.DEPLOYED", {"s": "svc:a", "o": "dep:d1", "vf": w.clock, "supersede": False}, "ops-1")
    return w
