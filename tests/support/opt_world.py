"""Optimizer test world (A12): T-C switched on by logged rewrites over a seeded ownership graph.

`golden_opt` is built once per session: the A06 agents, 20 teams, 1500 services and one OWNED_BY fact per service,
all through the kernel in replay mode (so the log replays, K4/K7 can rebuild it). Each test copies it and then
advances the injected clock past the optimizer window, so the seeding writes are outside the write mix. `ow`
activates T-C the way a tenant does: M3 for IndexChange and IndexSettled, M6 for the optimizer and the reconciler,
M5 for the measured scorer Opt.queryCost.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.support.ir_world import AGENTS, IR

ROOT = Path(__file__).resolve().parents[2]
INF = 9007199254740991
N_SERVICES = 1000
N_TEAMS = 20
VERSIONS = 4  # ownership versions per service: 1 + 2 * 3 = 7 OWNED_BY edges each
SPAN = N_SERVICES * VERSIONS * 1_000  # width of the valid-from range of the golden facts
VF0 = 1_791_000_000_000 - 1_000_000_000  # valid-from of the first ownership fact
VF_STEP = 1_000
WINDOW_MS = 600_000  # the optimizer window the tests use
GAP_MS = 2_000_000  # the clock jump after seeding, so the seeding is outside the window
OPT, REC = "opt-1", "rec-1"
Q5_FILE = ROOT / "gnomon" / "query" / "reads" / "q5.cypher"


@dataclass
class Golden:
    key: str
    clock: int


@pytest.fixture(scope="session")
def golden_opt(db, redis_raw, golden_genesis):
    from gnomon.kernel.service import KernelHandle
    from tests.conftest import _delete_tenant_graphs, kernel_test_registry

    key = f"t:test-optgolden-{uuid.uuid4().hex[:8]}"
    redis_raw.execute_command("GRAPH.COPY", golden_genesis, key)
    w = IR(KernelHandle(db.select_graph(key), kernel_test_registry()), redis_raw)
    for aid, role, skills in AGENTS:
        w.ok("RegisterAgent", {"op": "register", "id": aid, "role": role, "skills": skills}, "ops-1")
    for j in range(N_TEAMS):
        w.ok("RegisterEntity.Team", {"id": f"t{j}", "name": f"t{j}"}, "ops-1")
    for i in range(N_SERVICES):
        w.ok("RegisterEntity.Service", {"id": f"s{i}", "name": f"s{i}", "tier": "t1", "criticality": 3,
                                        "user_facing": True}, "ops-1")
    for j in range(VERSIONS):
        for i in range(N_SERVICES):
            p = {"s": f"svc:s{i}", "o": f"team:t{(i + j) % N_TEAMS}", "vf": VF0 + (j * N_SERVICES + i) * VF_STEP,
                 "supersede": j > 0}
            if j:
                p["old_o"] = f"team:t{(i + j - 1) % N_TEAMS}"
            w.ok("AssertFact.OWNED_BY", p, "ops-1")
    yield Golden(key, w.clock)
    _delete_tenant_graphs(redis_raw, key)


class RecordedMeasurer:
    """A measurement source with fixed samples: the decision envelope is then a pure function of the graph."""

    def __init__(self, base=1000, cand=200, write_base=50, write_cand=60, build_us=12_000):
        self.base, self.cand, self.write_base, self.write_cand, self.build_us = base, cand, write_base, write_cand, build_us

    def read(self, graph, text, params, warmup, runs):
        return [self.base if graph.name.endswith((":base", ":basew")) else self.cand] * runs

    def write(self, calls, graph):
        for call in calls:
            call()
        return [self.write_base if graph.name.endswith(":basew") else self.write_cand] * len(calls)

    def time(self, fn):
        return fn(), self.build_us


@dataclass
class OW:
    ir: IR
    db: object
    client: object
    key: str
    cfg: object
    telemetry: object = None
    qs: object = None
    q5_runs: int = 0
    hooks: dict = field(default_factory=dict)

    @property
    def k(self):
        return self.ir.k

    # ── time
    def tick(self) -> int:
        self.ir.clock += 10
        return self.ir.clock

    def now(self) -> int:
        return self.ir.clock

    # ── activation (M3, M6, M5)
    def activate(self) -> None:
        from gnomon.kernel.registry import layer_rules, register_rule_params
        from gnomon.kernel.service import KernelService
        from gnomon.optimizer import scorer

        rules = [*layer_rules("TA"), *layer_rules("TC")]  # T-A (decisions, scorers) is the base T-C stands on
        self.k.registry = self.k.registry.with_rules(*rules)
        self.k.service = KernelService(self.k.graph, self.k.registry)
        for rule in rules:
            self.ir.ok("RegisterRule", register_rule_params(rule), "ops-1", pid=f"m3-{rule.name}")
        self.ir.ok("RegisterAgent", {"op": "register", "id": OPT, "role": "optimizer", "skills": []}, "ops-1")
        self.ir.ok("RegisterAgent", {"op": "register", "id": REC, "role": "reconciler", "skills": []}, "ops-1")
        self.ir.ok("RegisterScorer", {"name": scorer.NAME, "sha": scorer.sha(), "kind": "measured", "version": 1},
                   "ops-1")

    # ── workload
    def reads(self, n: int, width: int = 20_000) -> None:
        """n real Q5 reads through the query service (telemetry on)."""
        session = self.qs.session("inv-1")
        for i in range(n):
            lo = VF0 + ((self.q5_runs + i) * 7_000) % (SPAN - width)
            self.qs.as_of(session, "Q5", {"T1": lo, "T2": lo + width})
        self.q5_runs += n

    def seed_shape(self, cypher: str, params: dict, n: int, ms: float = 2.0) -> None:
        """n synthetic telemetry entries of one shape with a fixed internal time (for deterministic workloads)."""
        for _ in range(n):
            self.telemetry.record(cypher, params, ms, 1, "inv-1")

    def seed_q5(self, n: int, ms: float = 2.0) -> None:
        from gnomon.kernel.service import wire_text

        text = wire_text(Q5_FILE.read_text(encoding="utf-8"))
        for i in range(n):
            lo = VF0 + (i * 7_000) % (SPAN - 20_000)
            self.telemetry.record(text, {"INF": INF, "T1": lo, "T2": lo + 20_000}, ms, 49, "inv-1")

    def reassign_owners(self, n: int) -> None:
        """n real OWNED_BY hand-overs inside the optimizer window (the write mix of the cost model)."""
        for i in range(n):
            now_owner = self.k.read("MATCH (s:Service {id: $s})-[f:OWNED_BY]->(t:Team) WHERE f.te = $INF AND f.vt = $INF "
                                    "RETURN t.id", {"s": f"s{i}", "INF": INF})[0][0]
            nxt = f"t{(int(now_owner[1:]) + 1) % N_TEAMS}"
            self.ir.ok("AssertFact.OWNED_BY", {"s": f"svc:s{i}", "o": f"team:{nxt}", "vf": self.ir.clock - 1_000,
                                               "supersede": True, "old_o": f"team:{now_owner}"}, "ops-1")

    # ── actors
    def optimizer(self, **kw):
        from gnomon.optimizer.engine import Optimizer

        kw.setdefault("config", self.cfg)
        return Optimizer(self.db, self.client, self.k, agent=OPT, mode="replay", clock=self.tick, **kw)

    def reconciler(self, **kw):
        from gnomon.ops.reconciler import Reconciler

        kw.setdefault("config", self.cfg)
        return Reconciler(self.db, self.client, self.k, agent=REC, mode="replay", clock=self.tick, **kw)

    # ── catalogue
    def entry(self, key: str) -> dict | None:
        rows = self.k.read("MATCH (x:IndexEntry {key: $k}) RETURN properties(x)", {"k": key})
        return dict(rows[0][0]) if rows else None

    def physical(self) -> set[str]:
        from gnomon.bootstrap import ddl

        return ddl.physical_catalogue(self.k.graph)[0]

    def scratch(self) -> list[str]:
        return sorted(g for g in self.client.execute_command("GRAPH.LIST") if g.startswith(self.key + ":opt:"))

    # ── hand-made index decisions (M1 / M2)
    def envelope(self, op, entity, label, prop, did=None, **over) -> dict:
        from gnomon.executive import envelope
        from gnomon.optimizer import scorer

        key = f"{entity}:{label}:{prop}"
        did = did or f"D-ix-{self.k.head()['seq']}-{op}-{label}-{prop}"
        params = {"op": op, "entity": entity, "label": label, "prop": prop, "key": key}
        fields = {
            "did": did, "attempt": 1, "aid": f"{did}/1", "kind": "index", "proposer": "optimizer",
            "rationale_ref": "opt:test", "scorer": scorer.NAME, "scorer_sha": scorer.sha(),
            "read_seq": self.k.head()["seq"], "hops": 0, "weight_scale": 1, "lambda_n": 1, "lambda_d": 1, "tau": 0,
            "margin": 5000, "baseline": {}, "evidence": [], "cites": [], "footprint_digest": "f0",
            "workload_digest": None, "channels": {},
            "candidates": [
                {"k": 0, "rule": "NoChange", "params": {}, "params_sha": envelope.params_sha({}), "outcome": "scored",
                 "benefit": 0, "harm": 0, "teams_new": 0, "score_num": 0, "won": False},
                {"k": 1, "rule": "IndexChange", "params": params, "params_sha": envelope.params_sha(params),
                 "outcome": "scored", "benefit": 9000, "harm": 0, "teams_new": 0, "score_num": 9000, "won": True}],
        }
        fields.update(over)
        return envelope.seal(fields)

    def m1(self, op, entity, label, prop, *, at=None, agent=OPT, env=None):
        from gnomon.executive import envelope

        d = env or self.envelope(op, entity, label, prop)
        key = f"{entity}:{label}:{prop}"
        p = envelope.params_for({"op": op, "entity": entity, "label": label, "prop": prop, "key": key}, d)
        self.ir.clock = at if at is not None else self.ir.clock + 10
        return self.k.submit("IndexChange", p, pid=envelope.pid_of(d["did"]), agent=agent, read_seq=d["read_seq"],
                             view_keys=[f"Index:{key}", "Index:*"], attach=envelope.attach_of(d), mode="replay",
                             rec_pt=self.ir.clock, rec_lc=0)

    def m2(self, key, outcome, *, agent=REC, at=None, pid=None, detail=""):
        e = self.entry(key)
        self.ir.clock = at if at is not None else self.ir.clock + 10
        return self.k.submit("IndexSettled", {"key": key, "outcome": outcome, "ddl_ms": 7, "detail": detail},
                             pid=pid or f"settle:{key}:{e['requested_seq']}", agent=agent, mode="replay",
                             rec_pt=self.ir.clock, rec_lc=0)

    def logical_index(self, entity, label, prop, state="operational") -> str:
        """An optimizer IndexEntry reached by M1 + M2 only (no DDL): for the guard and transition tests."""
        key = f"{entity}:{label}:{prop}"
        assert self.m1("create", entity, label, prop).code == "APPLIED"
        if state != "requested":
            assert self.m2(key, state).code == "APPLIED"
        return key

    def build_index(self, entity, label, prop) -> str:
        """A real optimizer index: M1, then the reconciler runs the DDL and settles it with M2."""
        key = f"{entity}:{label}:{prop}"
        assert self.m1("create", entity, label, prop).code == "APPLIED"
        self.reconciler().tick()
        assert self.entry(key)["state"] == "operational", self.entry(key)
        return key


@pytest.fixture
def make_ow(db, redis_raw, golden_opt):
    """A factory of optimizer worlds (copies of the seeded golden graph); everything is deleted afterwards."""
    from contextlib import ExitStack

    from gnomon.kernel.service import KernelHandle
    from gnomon.optimizer.config import OptConfig
    from gnomon.optimizer.workload import Telemetry
    from gnomon.query.service import QueryService
    from tests.conftest import kernel_test_registry, tenant_scope
    from tests.support.query_world import replay_clock

    with ExitStack() as stack:
        keys = []

        def make(**cfg_over) -> OW:
            key = stack.enter_context(tenant_scope(redis_raw))
            keys.append(key)
            redis_raw.execute_command("GRAPH.COPY", golden_opt.key, key)
            k = KernelHandle(db.select_graph(key), kernel_test_registry())
            ir = IR(k, redis_raw, clock=golden_opt.clock + GAP_MS, pids=itertools.count(10**6))
            cfg = OptConfig(**{"warmup": 2, "runs": 9, "kappa": 1, "margin_us": 1_000, "window_ms": WINDOW_MS,
                               "drop_windows": 2, "cooldown_ms": 60_000, **cfg_over})
            w = OW(ir, db, redis_raw, key, cfg)
            w.telemetry = Telemetry(redis_raw, key, clock=lambda: ir.clock)
            w.qs = QueryService(k, mode="replay", clock=replay_clock(ir), telemetry=w.telemetry)
            w.activate()
            return w

        yield make
        for key in keys:
            redis_raw.delete(f"{key}:wl", f"{key}:optstate")


@pytest.fixture
def ow(make_ow) -> OW:
    return make_ow()
