"""Executive test world (A11): T-A and T-B switched on by logged rewrites, on a small incident-response graph.

Services (criticality in brackets): a [3] depends on b [3] depends on c [2]; f [4] depends softly on c; c-r1 and c-r2
are replicas of c. c is down. Owners: a t1, b t2, c t2, f t3. Incident i1 affects a. Hand-computed integer weights
(W = 2520, level l weighs W/(l+1)): c 2*2*2520 = 10080, b 3*2*1260 = 7560, a 3*2*840 = 5040, f 4*1*1260 = 5040.

Every Executive in the tests scores with its own copy of udf/exec.js under a private library name, so a test that
replaces a library (MU22) cannot disturb a concurrent worker (FACTS S22).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support.decision_world import DW, activate
from tests.support.ir_world import IR

ROOT = Path(__file__).resolve().parents[2]
EXEC_SOURCE = ROOT / "udf" / "exec.js"
W = 2520
BASE_WEIGHTS = {"svc:c": 10080, "svc:b": 7560, "svc:a": 5040, "svc:f": 5040}
SVC = {"criticality": 3, "tier": "t1", "user_facing": True}


@dataclass
class XW:
    dw: DW
    library: str
    db: object
    redis: object
    deploys: dict

    @property
    def ir(self) -> IR:
        return self.dw.ir

    @property
    def k(self):
        return self.dw.k

    def executive(self, **kw):
        from gnomon.executive.executive import Executive

        kw.setdefault("agent", "exe-1")
        kw.setdefault("mode", "replay")
        kw.setdefault("library", self.library)
        kw.setdefault("clock", self.ir_clock)
        return Executive(self.db, self.redis, self.k, **kw)

    def ir_clock(self) -> int:
        self.ir.clock += 10
        return self.ir.clock

    def sim_graphs(self) -> list[str]:
        return [g for g in self.redis.execute_command("GRAPH.LIST") if ":sim:" in g and g.startswith(self.k.graph.name)]


_SHARED: dict[str, str] = {}


def exec_library(redis_raw, fresh: bool = False) -> str:
    """A copy of udf/exec.js under a library name private to this process; `fresh` makes a new one for a test that
    replaces its library. Loading a library is rare and global to the server, so the common case reuses one (FACTS S22)."""
    from gnomon.udf import loader

    if not fresh and "name" in _SHARED:
        return _SHARED["name"]
    name = f"Exec{uuid.uuid4().hex[:8]}"
    loader.load(redis_raw, name, EXEC_SOURCE.read_text(encoding="utf-8"))
    if not fresh:
        _SHARED["name"] = name
    return name


def build(ir: IR, db, redis_raw, *, root_cause=False, replicas=("ok", "down"), soft=True, fresh=False,
          c_health="down", scorer_version=1) -> XW:
    from gnomon.kernel.registry import layer_rules, register_rule_params
    from gnomon.kernel.service import KernelService
    from gnomon.udf import loader

    dw = activate(ir)
    library = exec_library(redis_raw, fresh)
    rules = layer_rules("TB")
    ir.k.registry = ir.k.registry.with_rules(*rules)
    ir.k.service = KernelService(ir.k.graph, ir.k.registry)
    ir.ok("RegisterScorer", {"name": f"{library}.blastRadius", "sha": loader.library_sha(redis_raw, library),
                             "kind": "udf", "version": scorer_version}, "ops-1")
    for rule in rules:
        ir.ok("RegisterRule", register_rule_params(rule), "ops-1", pid=f"m3-{rule.name}")
    for team in ("t2", "t3"):
        ir.ok("RegisterEntity.Team", {"id": team, "name": team}, "ops-1")
    for sid, crit in (("c", 2), ("f", 4)):
        ir.ok("RegisterEntity.Service", {"id": sid, "name": sid, **SVC, "criticality": crit}, "ops-1")
    for rid in ("c-r1", "c-r2"):
        ir.ok("RegisterEntity.Service", {"id": rid, "name": rid, **SVC, "criticality": 1}, "ops-1")
    edges = [("a", "b", True), ("b", "c", True)] + ([("f", "c", False)] if soft else [])
    for s, o, hard in edges:
        ir.ok("AssertFact.DEPENDS_ON", {"s": f"svc:{s}", "o": f"svc:{o}", "vf": ir.clock, "supersede": False,
                                         "hard": hard}, "ops-1")
    for s, team in (("b", "t2"), ("c", "t2"), ("f", "t3")):
        ir.ok("AssertFact.OWNED_BY", {"s": f"svc:{s}", "o": f"team:{team}", "vf": ir.clock, "supersede": False},
              "ops-1")
    for rid, health in zip(("c-r1", "c-r2"), replicas):
        ir.ok("AssertFact.REPLICA_OF", {"s": f"svc:{rid}", "o": "svc:c", "vf": ir.clock, "supersede": False}, "ops-1")
        if health != "ok":
            ir.ok("Observe.health", {"source_id": f"h-{rid}", "service": rid, "health": health,
                                     "observed_pt": ir.clock}, "ing-1")
    ir.ok("Observe.health", {"source_id": "h-c", "service": "c", "health": c_health, "observed_pt": ir.clock},
          "ing-1")
    deploys = {}
    if root_cause:
        for did, verified, version in (("c-prev", True, "1"), ("c-bad", False, "2")):
            ir.ok("RegisterEntity.Deploy", {"id": did, "service": "c", "version": version, "verified": verified,
                                            "expected_health": "ok"}, "ops-1")
        ir.ok("AssertFact.DEPLOYED", {"s": "svc:c", "o": "dep:c-prev", "vf": ir.clock, "supersede": False}, "ops-1")
        ir.ok("AssertFact.DEPLOYED", {"s": "svc:c", "o": "dep:c-bad", "vf": ir.clock + 5, "supersede": True,
                                      "old_o": "dep:c-prev"}, "ops-1")
        ir.ok("ProposeCause", {"incident": "i1", "cause": "dep:c-bad", "confidence": 80, "cites": []}, "inv-1")
        deploys = {"bad": "c-bad", "prev": "c-prev"}
    return XW(dw, library, db, redis_raw, deploys)


@pytest.fixture
def xw(opened, db, redis_raw) -> XW:
    return build(opened, db, redis_raw)


@pytest.fixture
def xw_rollback(opened, db, redis_raw) -> XW:
    """No healthy replica to fail over to and a root cause on c, so a gated rollback is the best action."""
    return build(opened, db, redis_raw, root_cause=True, replicas=("degraded", "down"))


@pytest.fixture
def xw_private(opened, db, redis_raw) -> XW:
    """Like `xw`, with a scorer library of its own, for tests that replace the library."""
    return build(opened, db, redis_raw, fresh=True)
