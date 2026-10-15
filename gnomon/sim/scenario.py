"""The Core scenario runner (master §11.4, §13, §26): world, infrastructure, ops loops and four LLM roles.

Everything runs in replay mode on one simulated clock, so a run is a pure function of (seed, recorded responses).
Rounds: the infrastructure's due alerts and health events are ingested, the scheduler expires dead leases, the
dispatcher delivers the outbox, then each agent takes one step. When the log holds a current root cause the
operator rolls the bad deploy back on the infrastructure (the action itself is outside the graph in Core).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from gnomon.agents import comms, investigator, triage
from gnomon.agents.llm import AgentLoop
from gnomon.audit.canonical import state_hash
from gnomon.ops.dispatcher import Dispatcher
from gnomon.ops.scheduler import Scheduler
from gnomon.query.service import QueryService
from gnomon.sim.generator import apply_world, generate
from gnomon.sim.infra import Infra

CONFIG = Path(__file__).parent / "scenarios" / "core.yaml"
ROLES = {"triage-1": triage.ROLE, "inv-a": investigator.ROLE, "inv-b": investigator.ROLE, "com-1": comms.ROLE}
INF = 9007199254740991
GENESIS_PT = 1_791_000_000_000


def load_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


class SimClock:
    """Simulated wall clock in epoch ms (contract C8). `tick` is a proposal time 10 ms after the last one."""

    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def tick(self) -> int:
        self.now += 10
        return self.now

    def step(self, ms: int) -> int:
        self.now += ms
        return self.now


@dataclass
class CoreResult:
    kernel: object
    state_hash: str
    resolved: bool
    incident: str | None
    bad_deploy: dict
    infra: Infra
    rounds: int
    rules_applied: list
    log: list
    crash: dict | None
    load: object
    llm_calls: int
    llm_misses: int
    surfaced: list = field(default_factory=list)

    def summary(self) -> dict:
        crash = None
        if self.crash is not None:
            crash = {k: (v.code.value if hasattr(v, "code") else v) for k, v in self.crash.items()}
        return {"resolved": self.resolved, "incident": self.incident, "rounds": self.rounds,
                "state_hash": self.state_hash, "bad_deploy": self.bad_deploy, "crash": crash,
                "llm_calls": self.llm_calls, "llm_misses": self.llm_misses,
                "rules_applied": sorted(self.rules_applied), "surfaced": self.surfaced}


def run_core(kernel, *, seed: int | None = None, llm, crash: bool | None = None, n_services: int | None = None,
             max_rounds: int | None = None) -> CoreResult:
    cfg = load_config()
    seed = cfg["seed"] if seed is None else seed
    crash = cfg["crash"] if crash is None else crash
    world = generate(seed, n_services or cfg["n_services"], cfg["n_teams"])
    load = apply_world(kernel, world)
    if load.rejected:
        raise RuntimeError(f"the world did not load: {load.rejected[:3]}")
    clock = SimClock(load.end_pt)
    for agent in cfg["agents"]:
        out = kernel.submit("RegisterAgent", {"op": "register", **agent}, pid=f"w-agent-{agent['id']}", agent="ops-1",
                            mode="replay", rec_pt=clock.tick(), rec_lc=0)
        if out.code.value != "APPLIED":
            raise RuntimeError(f"cannot register {agent['id']}: {out}")
    qs = QueryService(kernel, mode="replay", clock=clock.tick)
    infra = Infra(seed, world)
    infra.start(clock.now)
    scheduler = Scheduler(kernel, clock, agent="sch-1")
    dispatcher = Dispatcher(kernel, infra, clock, agent="dsp-1")
    agents = {aid: AgentLoop(aid, ROLES[aid], qs, llm) for aid in cfg["order"]}
    log: list = []
    crashed: dict | None = None
    rolled_back = False
    bad = world.bad_deploy
    rounds = 0

    def ingest(rule: str, event: dict) -> None:
        out = kernel.submit(rule, dict(event), pid=f"ing:{event['source_id']}", agent="ing-1", mode="replay",
                            rec_pt=clock.tick(), rec_lc=0)
        log.append((rounds, "ing-1", rule, out.code.value))

    for rounds in range(1, (max_rounds or cfg["max_rounds"]) + 1):
        clock.step(cfg["round_ms"])
        for alert in infra.due_alerts(clock.now):
            ingest("Observe.alert", alert)
        for event in infra.due_events(clock.now):
            ingest("Observe.health", event)
        for _, out in scheduler.tick():
            log.append((rounds, "sch-1", "ExpireLease", out.code.value))
        for done in dispatcher.tick():
            log.append((rounds, "dsp-1", "Observe.receipt", done["receipt"].code.value))
        for aid in list(agents):
            res = agents[aid].step()
            log.append((rounds, aid, res.rule, res.state))
            if crash and crashed is None and aid == "inv-a" and res.rule == "ClaimTask" and res.state == "applied":
                # kill -9: the process is gone; only its session and last request survive for the replay
                crashed = {"agent": aid, "rule": res.rule, "pid": res.pid, "task": res.tuple["task"],
                           "loop": agents.pop(aid)}
        if not rolled_back and kernel.read(
                "MATCH (:Incident {state: 'open'})-[rc:ROOT_CAUSE]->() WHERE rc.te = $inf RETURN count(rc)",
                {"inf": INF})[0][0]:
            infra.act(f"rb:{bad['service']}", "rollback", bad["service"], now=clock.now)
            rolled_back = True
        if kernel.read("MATCH (i:Incident {state: 'resolved'}) RETURN count(i)")[0][0]:
            break
    for _ in range(3):  # let the outbox drain
        clock.step(cfg["round_ms"])
        scheduler.tick()
        dispatcher.tick()

    crash_info = None
    if crashed is not None:
        loop = crashed.pop("loop")
        crash_info = {**crashed, "replay": qs.propose(loop.session, **loop.last_request)}
    incident = kernel.read("MATCH (i:Incident) RETURN i.id ORDER BY i.id LIMIT 1")
    resolved = bool(kernel.read("MATCH (i:Incident {state: 'resolved'}) RETURN count(i)")[0][0])
    return CoreResult(
        kernel=kernel, state_hash=state_hash(kernel.graph), resolved=resolved,
        incident=incident[0][0] if incident else None, bad_deploy=bad, infra=infra, rounds=rounds,
        rules_applied=[r[0] for r in kernel.read("MATCH (r:Rewrite) RETURN DISTINCT r.rule ORDER BY r.rule")],
        log=log, crash=crash_info, load=load, llm_calls=getattr(llm, "calls", 0),
        llm_misses=getattr(llm, "misses", 0), surfaced=[s for a in agents.values() for s in a.surfaced])


def bootstrap_demo(db, client, key: str):
    """Bootstrap DDL and R0 for a demo tenant with the core registry; returns a KernelHandle on `key`."""
    from gnomon.bootstrap.genesis import bootstrap
    from gnomon.kernel.registry import load_registry
    from gnomon.kernel.service import KernelHandle
    from gnomon.udf import loader

    loader.load(client)
    registry = load_registry()
    agents = [{"id": "kernel", "role": "kernel", "skills": []},
              {"id": "triage-1", "role": "triage", "skills": ["triage"]},
              {"id": "ops-1", "role": "operator", "skills": ["ops", "triage"]}]
    graph = db.select_graph(key)
    out = bootstrap(graph, client, registry, tenant="demo", agents=agents, skills=["ops", "triage"], pid="genesis",
                    mode="replay", rec_pt=GENESIS_PT, rec_lc=0)
    if out.code.value != "APPLIED":
        raise RuntimeError(f"genesis failed: {out}")
    return KernelHandle(graph, registry)
