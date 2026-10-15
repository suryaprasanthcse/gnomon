"""Audit scenario (A07; imported by tests/integration/audit and tests/mutation, DECISIONS D-28).

One incident-response history that exercises every element the K1–K6 audits and the MU1–MU18
mutations look at: an open lease, a current and a voided fact version, a DEP_NOW projection, a
citation, an outbox row, a root cause, and CAUSED_BY edges from proposals that cite earlier writes
as view keys. The world is built once per test module on its own tenant key; tests copy it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from tests.support.ir_world import AGENTS, IR

INF = 9007199254740991


def _delete_tenant_graphs(client, key: str) -> None:
    for name in client.execute_command("GRAPH.LIST"):
        if name == key or name.startswith(key + ":"):
            client.execute_command("GRAPH.DELETE", name)


@dataclass(frozen=True)
class Scenario:
    key: str
    head_seq: int
    head_hash: str
    message_uid: str
    voided_fid: str
    cited_fid: str


def build(ir: IR) -> dict:
    """Runs the scenario on `ir`; returns the identifiers tests refer to."""
    for aid, role, skills in AGENTS:
        ir.ok("RegisterAgent", {"op": "register", "id": aid, "role": role, "skills": skills}, "ops-1")
    for sid in ("a", "b"):
        ir.ok("RegisterEntity.Service", {"id": sid, "name": sid, "tier": "t1", "criticality": 3,
                                         "user_facing": True}, "ops-1")
    ir.ok("RegisterEntity.Team", {"id": "t1", "name": "t1"}, "ops-1")
    ir.ok("RegisterEntity.Deploy", {"id": "d1", "service": "a", "version": "1", "verified": True,
                                    "expected_health": "ok"}, "ops-1")
    ir.ok("AssertFact.OWNED_BY", {"s": "svc:a", "o": "team:t1", "vf": ir.clock, "supersede": False}, "ops-1")
    ir.ok("AssertFact.DEPENDS_ON", {"s": "svc:a", "o": "svc:b", "vf": ir.clock, "supersede": False, "hard": True},
          "ops-1", pid="dep-ab")
    ir.ok("AssertFact.DEPENDS_ON", {"s": "svc:b", "o": "svc:a", "vf": ir.clock, "supersede": False, "hard": False},
          "ops-1", pid="dep-ba")
    voided = ir.one("MATCH (:Service {id: 'b'})-[f:DEPENDS_ON]->(:Service {id: 'a'}) RETURN f.fid")[0]
    ir.ok("RetractFact.DEPENDS_ON", {"s": "svc:b", "o": "svc:a", "fid": voided, "mode": "void"}, "ops-1")
    ir.alert()
    ir.incident(tasks=[{"id": "t1", "kind": "investigate", "skill": "ops"},
                       {"id": "t2", "kind": "investigate", "skill": "ops"}], pid="inc-1")
    head = ir.k.head()["seq"]
    # Proposals that read earlier writes: each one's view keys are a predecessor's write keys.
    ir.ok("ClaimTask", {"agent": "inv-1", "task": "t1", "ttl_ms": 600_000}, "inv-1", pid="cl-1",
          view_keys=ir.write_keys("inc-1"), read_seq=head)
    ir.ok("ClaimTask", {"agent": "rsp-1", "task": "t2", "ttl_ms": 600_000}, "rsp-1", pid="cl-2")
    ir.ok("CompleteTask", {"task": "t2", "result": "looked fine"}, "rsp-1", pid="co-2")
    cited = ir.owned_by_fid()
    head = ir.k.head()["seq"]
    ir.ok("PostUpdate", {"incident": "i1", "message_id": "m1", "body": "investigating",
                         "cites": [{"s": "svc:a", "fid": cited}]}, "com-1", pid="pu-1",
          view_keys=ir.write_keys("cl-1"), read_seq=head)
    ir.ok("ProposeCause", {"incident": "i1", "cause": "dep:d1", "confidence": 70, "cites": []}, "inv-1",
          pid="pc-1")
    return {"message_uid": "msg:m1", "voided_fid": voided, "cited_fid": cited}


@pytest.fixture(scope="module")
def scenario(db, redis_raw, golden_genesis, genesis_config):
    from gnomon.kernel.service import KernelHandle

    key = f"t:test-{uuid.uuid4().hex[:8]}"
    try:
        redis_raw.execute_command("GRAPH.COPY", golden_genesis, key)
        ir = IR(KernelHandle(db.select_graph(key), genesis_config["registry"]), redis_raw)
        ids = build(ir)
        head = ir.k.head()
        yield Scenario(key=key, head_seq=head["seq"], head_hash=head["hash"], **ids)
    finally:
        _delete_tenant_graphs(redis_raw, key)


@pytest.fixture
def scratch(redis_raw, scenario):
    """A fresh copy of the scenario at t:<scenario>:replay:a07-<n>; deleted with the scenario's graphs."""
    key = f"{scenario.key}:replay:a07-{uuid.uuid4().hex[:6]}"
    redis_raw.execute_command("GRAPH.COPY", scenario.key, key)
    try:
        yield key
    finally:
        _delete_tenant_graphs(redis_raw, key)
