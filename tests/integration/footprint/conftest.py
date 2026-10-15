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
    outbox: str = ""

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

    # A06 rules (D-23): agents for every core role, two incidents, a live and an expired lease, an outbox.
    for aid, role, skills in [("ing-1", "ingest", []), ("dsp-1", "dispatcher", []), ("tri-9", "triage", ["ops"]),
                              ("inv-1", "investigator", ["ops"]), ("com-1", "comms", ["triage"]),
                              ("sch-1", "scheduler", []), ("hum-1", "human", [])]:
        apply("RegisterAgent", {"op": "register", "id": aid, "role": role, "skills": skills})

    def as_agent(rule, p, agent):
        out = k.submit(rule, p, pid=f"seed-{next(pids)}", agent=agent)
        assert out.code == "APPLIED", (rule, p, out)
        return out

    for sid, svc, sig in [("al-1", "a", "cpu"), ("al-2", "b", "disk"), ("al-3", "c", "net")]:
        as_agent("Observe.alert", {"source_id": sid, "service": svc, "signature": sig, "severity": "high",
                                   "observed_pt": SEED_VF, "payload_sha": "f" * 64}, "ing-1")
    as_agent("OpenIncident", {"alert": "al-1", "incident": "i1", "service": "a", "signature": "cpu",
                              "tasks": [{"id": "t1", "kind": "k", "skill": "ops"}, {"id": "t2", "kind": "k",
                                                                                     "skill": "ops"},
                                        {"id": "t3", "kind": "k", "skill": "triage"}]}, "triage-1")
    as_agent("ClaimTask", {"agent": "inv-1", "task": "t2", "ttl_ms": 900_000}, "inv-1")
    as_agent("ClaimTask", {"agent": "com-1", "task": "t3", "ttl_ms": 1}, "com-1")
    as_agent("OpenIncident", {"alert": "al-3", "incident": "i3", "service": "c", "signature": "net",
                              "tasks": [{"id": "t5", "kind": "k", "skill": "ops"}]}, "triage-1")
    as_agent("ClaimTask", {"agent": "inv-1", "task": "t5", "ttl_ms": 900_000}, "inv-1")
    as_agent("CompleteTask", {"task": "t5", "result": "r"}, "inv-1")
    as_agent("ProposeCause", {"incident": "i3", "cause": "svc:c", "confidence": 60, "cites": []}, "inv-1")
    post = k.submit("PostUpdate", {"incident": "i1", "message_id": "m1", "body": "b",
                                   "cites": [{"s": "svc:a", "fid": seed.fids["OWNED_BY"]}]}, pid="seed-post",
                    agent="com-1")
    assert post.code == "APPLIED", post
    seed.outbox = "seed-post"
    # A current ROOT_CAUSE version on incident i1, so RetractFact.ROOT_CAUSE can apply (R10 itself is verified too).
    rc = k.submit("ProposeCause", {"incident": "i1", "cause": "dep:d1", "confidence": 70, "cites": []},
                  pid="seed-rc", agent="inv-1")
    assert rc.code == "APPLIED", rc
    seed.fids["ROOT_CAUSE"] = "seed-rc#0"
    yield seed
    for name in redis_raw.execute_command("GRAPH.LIST"):
        if name == key or name.startswith(key + ":"):
            redis_raw.execute_command("GRAPH.DELETE", name)
