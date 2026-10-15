"""Footprint harness (A05): a seeded tenant for dynamic verification (§7.5) and graph snapshots.

The seed is a GRAPH.COPY of golden_genesis plus entities and one current version of each fact type,
all applied through the kernel. Each verification copies it to t:<seed>:replay:fpv-<n> (§7.5 step 1).
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field

import pytest

EPOCH = 1_791_000_000_000
SEED_VF = EPOCH + 100


@dataclass
class Seed:
    key: str
    registry: object
    fids: dict[str, str] = field(default_factory=dict)
    counter: itertools.count = field(default_factory=lambda: itertools.count(1))

    def scratch(self) -> str:
        return f"{self.key}:replay:fpv-{next(self.counter)}"


def _snapshot(graph) -> tuple:
    head = graph.query("MATCH (h:Head) RETURN h.seq, h.pt, h.lc, h.hash").result_set
    nodes = graph.query("MATCH (n) RETURN count(n)").result_set[0][0]
    edges = graph.query("MATCH ()-[e]->() RETURN count(e)").result_set[0][0]
    return head, nodes, edges


@pytest.fixture(scope="session")
def snap():
    return _snapshot


@pytest.fixture(scope="module")
def fpv_seed(db, redis_raw, golden_genesis, genesis_config):
    from gnomon.kernel.service import KernelHandle

    key = f"t:test-fpv-{uuid.uuid4().hex[:8]}"
    redis_raw.execute_command("GRAPH.COPY", golden_genesis, key)
    k = KernelHandle(db.select_graph(key), genesis_config["registry"])
    pids = itertools.count(1)

    def apply(rule, p):
        out = k.submit(rule, p, pid=f"seed-{next(pids)}", agent="ops-1")
        assert out.code == "APPLIED", (rule, p, out)
        return out

    for sid in ("a", "b", "c"):
        apply("RegisterEntity.Service", {"id": sid, "name": sid, "tier": "t1", "criticality": 3, "user_facing": True})
    for tid in ("t1", "t2"):
        apply("RegisterEntity.Team", {"id": tid, "name": tid})
    apply("RegisterEntity.ExternalService", {"id": "x1", "tenant": "acme", "remote_id": "r1", "contract_criticality": 2})
    apply("RegisterEntity.Deploy", {"id": "d1", "service": "a", "version": "1", "verified": True,
                                    "expected_health": "ok"})
    apply("RegisterEntity.Deploy", {"id": "d2", "service": "a", "version": "2", "verified": False,
                                    "expected_health": "degraded"})
    seed = Seed(key, genesis_config["registry"])
    facts = {
        "OWNED_BY": ("svc:a", "team:t1", {}), "DEPENDS_ON": ("svc:a", "svc:b", {"hard": True}),
        "DEPLOYED": ("svc:a", "dep:d1", {}), "REPLICA_OF": ("svc:b", "svc:c", {}),
        "MEMBER_OF": ("agent:ops-1", "team:t1", {}),
    }
    for rel, (s, o, extra) in facts.items():
        pid = f"seed-{rel.lower()}"
        out = k.submit(f"AssertFact.{rel}", {"s": s, "o": o, "vf": SEED_VF, "supersede": False, **extra}, pid=pid,
                       agent="ops-1")
        assert out.code == "APPLIED", (rel, out)
        seed.fids[rel] = f"{pid}#0"
    # ROOT_CAUSE is asserted by R10 (A06); seed one current version directly so RetractFact.ROOT_CAUSE can apply.
    k.graph.query(
        "CREATE (i:Incident:Entity {uid: 'inc:i1', id: 'i1', rw: 'seed'}) WITH i MATCH (d:Deploy {id: 'd1'}) "
        "CREATE (i)-[:ROOT_CAUSE {fid: 'seed-rc#0', vf: $vf, vt: $inf, tf: $vf, tt: $inf, ts: 1, te: $inf, "
        "rw: 'seed', by: 'agent:inv', confidence: 70}]->(d)", {"vf": SEED_VF, "inf": 9007199254740991})
    seed.fids["ROOT_CAUSE"] = "seed-rc#0"
    yield seed
    for name in redis_raw.execute_command("GRAPH.LIST"):
        if name == key or name.startswith(key + ":"):
            redis_raw.execute_command("GRAPH.DELETE", name)
