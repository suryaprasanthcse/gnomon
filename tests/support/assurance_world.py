"""Assurance world (A16): three federated tenants with every layer switched on, built once per worker and copied per test.

Each tenant A, B and C is a complete kernel graph (own log and chain) that is registered in the sys graph `t:_sys`, has
the T-D rules and a relay, the T-A decision framework, the T-B Executive with a version-2 scorer, and the T-C optimizer
and reconciler agents; its incident world is the Executive world of A11 (an open incident i1 on service a, service c
degraded with a healthy replica). All proposals run in replay mode, so every state hash is a pure function of the steps.

`AW` is the Fed harness of the A13 tests plus the per-tenant incident, Executive and optimizer handles.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field

import pytest

from tests.integration.test_a13_federation import (  # noqa: F401  (the A13 harness and its constants)
    EPOCH, INGEST, KINDS, OPERATOR, PAIRS, RELAY, TENANTS, TRIAGE, Fed, peering,
)
from tests.support import exec_world
from tests.support.ir_world import AGENTS as IR_AGENTS
from tests.support.ir_world import IR

BOOT_AGENTS = [
    {"id": "kernel", "role": "kernel", "skills": []},
    {"id": "ops-1", "role": "operator", "skills": ["ops", "triage"]},
    {"id": "triage-1", "role": "triage", "skills": ["triage"]},
    {"id": TRIAGE, "role": "triage", "skills": ["triage"]},
]
BOOT_SKILLS = ["ops", "triage"]
OPT, REC = "opt-1", "rec-1"
PID_BASE = 10**6  # IR proposal ids start here so they never meet a Fed pid


def layered_registry():
    """Core + T-D + T-A + T-B + T-C and the two test rules T-A's own tests use."""
    from gnomon.federation import channels
    from gnomon.kernel.registry import layer_rules, load_rule_file

    base = channels.td_registry()
    rules = [*layer_rules("TA"), *layer_rules("TB"), *layer_rules("TC"),
             load_rule_file(exec_world.ROOT / "tests" / "fixtures" / "rules" / "noop_action.yaml"),
             load_rule_file(exec_world.ROOT / "tests" / "fixtures" / "rules" / "noop_action_gated.yaml")]
    return base.with_rules(*rules)


@dataclass
class AW(Fed):
    irs: dict = field(default_factory=dict)
    xws: dict = field(default_factory=dict)
    execs: dict = field(default_factory=dict)
    library: str = ""

    # ── per-tenant handles
    def sync(self, t: str) -> IR:
        """The tenant's IR with its clock moved past the head, so a proposal never regresses the HLC."""
        ir = self.irs[t]
        ir.clock = max(ir.clock, self.k[t].head()["pt"], self.clock) + 10
        return ir

    def ir_ok(self, t, rule, p, agent="ops-1", **kw):
        ir = self.sync(t)
        out = ir.submit(rule, p, agent, **kw)
        assert out.code == "APPLIED", (t, rule, p, out)
        return out

    def ir_try(self, t, rule, p, agent="ops-1", **kw):
        return self.sync(t).submit(rule, p, agent, **kw)

    def executive(self, t):
        if t not in self.execs:
            self.execs[t] = self.xws[t].executive(federation=True)
        self.sync(t)
        return self.execs[t]

    def optimizer(self, t):
        from gnomon.optimizer.config import OptConfig
        from gnomon.optimizer.engine import Optimizer

        ir = self.sync(t)

        def tick() -> int:
            ir.clock += 10
            return ir.clock

        return Optimizer(self.db, self.client, self.k[t], agent=OPT, mode="replay", clock=tick,
                         config=OptConfig(window_ms=600_000, cooldown_ms=60_000, warmup=1, runs=3))

    def reconciler(self, t):
        from gnomon.ops.reconciler import Reconciler

        ir = self.sync(t)

        def tick() -> int:
            ir.clock += 10
            return ir.clock

        return Reconciler(self.db, self.client, self.k[t], agent=REC, mode="replay", clock=tick)

    def attach_layers(self, t: str) -> None:
        """(Re)creates the IR/Executive handles of tenant `t` on its current graph and registry."""
        from gnomon.kernel.service import KernelHandle
        from tests.support.decision_world import DW
        from tests.support.exec_world import XW

        k = KernelHandle(self.db.select_graph(self.keys[t]), layered_registry())
        self.k[t] = k
        ir = IR(k, self.client, clock=k.head()["pt"] + 10, pids=itertools.count(2 * PID_BASE))  # past the golden build's pids
        self.irs[t] = ir
        self.xws[t] = XW(DW(ir), self.library, self.db, self.client, {})
        self.execs.pop(t, None)

    def full_audit(self):
        """K1-K8 on every tenant and K9 over the federation: the end state the stateful machine must reach."""
        from gnomon.audit.runner import audit

        reports = {t: audit(self.db, self.client, self.keys[t], ["K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8"])
                   for t in TENANTS}
        return reports, self.audit()

    def hashes(self) -> dict:
        from gnomon.audit.canonical import state_hash

        return {t: state_hash(self.db.select_graph(self.keys[t])) for t in TENANTS}

    def head_hashes(self) -> dict:
        return {t: self.k[t].head()["hash"] for t in TENANTS}


def _layer_tenant(world: AW, t: str) -> None:
    from gnomon.federation import channels
    from gnomon.kernel.registry import layer_rules, register_rule_params
    from gnomon.kernel.service import KernelService
    from gnomon.optimizer import scorer

    k = world.k[t]
    last = channels.activate(k, operator=OPERATOR, relay=RELAY, at=EPOCH + 2_000)
    ir = IR(k, world.client, clock=last + 10, pids=itertools.count(PID_BASE))
    for aid, role, skills in IR_AGENTS:
        ir.ok("RegisterAgent", {"op": "register", "id": aid, "role": role, "skills": skills}, OPERATOR)
    for sid in ("a", "b"):
        ir.ok("RegisterEntity.Service", {"id": sid, "name": sid, "tier": "t1", "criticality": 3, "user_facing": True},
              OPERATOR)
    ir.ok("RegisterEntity.Team", {"id": "t1", "name": "t1"}, OPERATOR)
    ir.ok("RegisterEntity.Deploy", {"id": "d1", "service": "a", "version": "1", "verified": True,
                                    "expected_health": "ok"}, OPERATOR)
    ir.ok("AssertFact.OWNED_BY", {"s": "svc:a", "o": "team:t1", "vf": ir.clock, "supersede": False}, OPERATOR)
    ir.alert()
    ir.incident()
    xw = exec_world.build(ir, world.db, world.client, c_health="degraded", scorer_version=2)
    world.library = xw.library
    rules = layer_rules("TC")
    k.registry = k.registry.with_rules(*rules)
    k.service = KernelService(k.graph, k.registry)
    for rule in rules:
        ir.ok("RegisterRule", register_rule_params(rule), OPERATOR, pid=f"m3-{rule.name}")
    ir.ok("RegisterAgent", {"op": "register", "id": OPT, "role": "optimizer", "skills": []}, OPERATOR)
    ir.ok("RegisterAgent", {"op": "register", "id": REC, "role": "reconciler", "skills": []}, OPERATOR)
    ir.ok("RegisterScorer", {"name": scorer.NAME, "sha": scorer.sha(), "kind": "measured", "version": 1}, OPERATOR)
    world.irs[t], world.xws[t] = ir, xw


@pytest.fixture(scope="session")
def aw_golden(db, redis_raw):
    """sys + A + B + C with every layer on and the channels open; built once per worker."""
    from gnomon.bootstrap.genesis import bootstrap
    from gnomon.federation import channels, sys as fsys
    from gnomon.kernel import composer
    from gnomon.kernel.registry import load_registry
    from gnomon.udf import loader

    loader.load(redis_raw)
    uid = uuid.uuid4().hex[:8]
    keys = {"sys": f"t:test-awg{uid}-sys", **{t: f"t:test-awg{uid}-{t}" for t in TENANTS}}
    try:
        assert fsys.bootstrap_sys(db, redis_raw, keys["sys"], mode="replay", rec_pt=EPOCH, rec_lc=0).code == "APPLIED"
        sysk = fsys.sys_handle(db, keys["sys"])
        pt = EPOCH
        for t in TENANTS:
            pt += 10
            assert sysk.submit("RegisterTenant", {"id": t, "kernel_sha": composer.kernel_sha()}, pid=f"s1-{t}",
                               agent=OPERATOR, mode="replay", rec_pt=pt, rec_lc=0).code == "APPLIED"
        for a, b in PAIRS:
            pt += 10
            assert sysk.submit("PeerTenants", {"a": a, "b": b, "kinds": KINDS}, pid=f"s2-{a}-{b}", agent=OPERATOR,
                               mode="replay", rec_pt=pt, rec_lc=0).code == "APPLIED"
        world = AW(db, redis_raw, keys)
        world.sysk = sysk
        for t in TENANTS:
            anchor = fsys.anchor_of(sysk, t)
            out = bootstrap(db.select_graph(keys[t]), redis_raw, load_registry(), tenant=t, agents=BOOT_AGENTS,
                            skills=BOOT_SKILLS, pid="genesis", mode="replay", rec_pt=EPOCH + 1_000, rec_lc=0,
                            sys_anchor=fsys.anchor_param(anchor))
            assert out.code == "APPLIED", (t, out)
            world.k[t] = channels.attach(db.select_graph(keys[t]))
            _layer_tenant(world, t)
        world.open_all()
        yield keys
    finally:
        for name in redis_raw.execute_command("GRAPH.LIST"):
            if name.startswith(f"t:test-awg{uid}-"):
                redis_raw.execute_command("GRAPH.DELETE", name)


@pytest.fixture
def make_aw(db, redis_raw, aw_golden):
    """A factory of private copies of the golden federation (for runs that need several); all are deleted afterwards."""
    from gnomon.federation import sys as fsys

    suffixes = []

    def make() -> AW:
        suffix = uuid.uuid4().hex[:8]
        suffixes.append(suffix)
        copies = {name: f"t:test-aw{suffix}-{name}" for name in aw_golden}
        for name, golden in aw_golden.items():
            redis_raw.execute_command("GRAPH.COPY", golden, copies[name])
        world = AW(db, redis_raw, copies)
        world.sysk = fsys.sys_handle(db, copies["sys"])
        world.library = exec_world._SHARED["name"]
        for t in TENANTS:
            world.attach_layers(t)
        return world

    try:
        yield make
    finally:
        for name in redis_raw.execute_command("GRAPH.LIST"):
            if any(name.startswith(f"t:test-aw{suffix}-") for suffix in suffixes):
                redis_raw.execute_command("GRAPH.DELETE", name)
        for suffix in suffixes:
            for t in TENANTS:
                redis_raw.delete(f"t:test-aw{suffix}-{t}:wl", f"t:test-aw{suffix}-{t}:optstate")


@pytest.fixture
def aw(make_aw):
    """A private copy of the golden federation, with its handles attached."""
    return make_aw()
