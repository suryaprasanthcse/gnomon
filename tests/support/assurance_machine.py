"""The all-layers interleaving (A16, master §22 "Stateful property"): seeded random steps across three tenants.

A step is one of: an agent-callable rule on a random tenant (health, dependency assert and retract, ownership, claim and
complete), `decide` or `simulate` of the Executive, an optimizer run with the reconciler, a notice, a new subscription,
a provider health change, a publish, a relay delivery or a relay sweep. After every step K5 must hold on each tenant the
step touched. The generator is `random.Random(seed)` and every proposal is in replay mode, so a seed is a script: the
same seed on the same starting world gives the same trace, state hashes, decision shas and federated log (C8).
"""

from __future__ import annotations

import random

from tests.integration.test_a13_federation import TENANTS
from tests.support.assurance_world import AW

SERVICES = ("a", "b", "c", "f", "c-r1", "c-r2")
HEALTHS = ("ok", "degraded", "down")
INF = 9007199254740991
WEIGHTS = {"health": 4, "dep": 2, "retract": 1, "owner": 2, "claim": 2, "complete": 1, "decide": 5, "simulate": 2,
           "optimize": 2, "notice": 2, "deliver": 4, "consume": 1, "provider_health": 2, "publish": 2, "sweep": 1}


class Machine:
    def __init__(self, aw: AW, seed: int) -> None:
        self.aw, self.rng, self.seed = aw, random.Random(seed), seed
        self.relay = aw.relay()
        self.n = 0
        self.trace: list[tuple] = []
        self.decisions: list[tuple] = []  # (tenant, did, state, outcome code, envelope sha)
        self.subscriptions: list[tuple] = []  # (consumer, provider, provider svc)
        self.k5_checks = 0

    @property
    def tag(self) -> str:
        """A name no other step or seed of the same world can reuse."""
        return f"{self.seed}-{self.n}"

    # ── driving
    def run(self, steps: int) -> Machine:
        names = sorted(WEIGHTS)
        weights = [WEIGHTS[name] for name in names]
        for _ in range(steps):
            name = self.rng.choices(names, weights)[0]
            self.n += 1
            touched, outcome = getattr(self, "do_" + name)()
            self.trace.append((self.n, name, tuple(touched), outcome))
            self.check_k5(touched)
        return self

    def check_k5(self, tenants) -> None:
        from gnomon.audit.runner import audit

        for t in dict.fromkeys(tenants):
            report = audit(self.aw.db, self.aw.client, self.aw.keys[t], ["K5"])
            assert report.ok, (self.seed, self.n, self.trace[-1], t, report.as_dict())
            self.k5_checks += 1

    def finish(self) -> None:
        """Deliver everything in flight; the caller then audits the end state."""
        self.relay.sweep()

    def pick(self, items):
        return self.rng.choice(sorted(items))

    # ── agent-callable rules
    def do_health(self):
        t, svc, health = self.pick(TENANTS), self.pick(SERVICES), self.pick(HEALTHS)
        ir = self.aw.sync(t)
        out = ir.submit("Observe.health", {"source_id": f"h{self.tag}", "service": svc, "health": health,
                                           "observed_pt": ir.clock}, "ing-1")
        return [t], f"{svc}:{health}:{out.code.value}"

    def do_dep(self):
        t = self.pick(TENANTS)
        s, o = self.rng.sample(SERVICES[:4], 2)
        out = self.aw.ir_try(t, "AssertFact.DEPENDS_ON", {"s": f"svc:{s}", "o": f"svc:{o}", "vf": self.aw.irs[t].clock,
                                                           "supersede": False, "hard": self.rng.random() < 0.5}, "ops-1")
        return [t], f"{s}->{o}:{out.code.value}"

    def do_retract(self):
        t = self.pick(TENANTS)
        rows = self.aw.k[t].read("MATCH (s:Service)-[f:DEPENDS_ON]->(o:Service) WHERE f.te = $inf AND f.vt = $inf "
                                 "RETURN s.uid, o.uid, f.fid ORDER BY f.fid", {"inf": INF})
        if not rows:
            return [], "none"
        s, o, fid = rows[self.rng.randrange(len(rows))]
        out = self.aw.ir_try(t, "RetractFact.DEPENDS_ON", {"s": s, "o": o, "fid": fid, "mode": "void"}, "ops-1")
        return [t], out.code.value

    def do_owner(self):
        t, svc, team = self.pick(TENANTS), self.pick(SERVICES[:4]), self.pick(("t1", "t2", "t3"))
        cur = self.aw.k[t].read("MATCH (:Service {id: $s})-[f:OWNED_BY]->(t:Team) WHERE f.te = $inf AND f.vt = $inf "
                                "RETURN t.uid", {"s": svc, "inf": INF})
        if cur and cur[0][0] == f"team:{team}":
            return [], "same"
        p = {"s": f"svc:{svc}", "o": f"team:{team}", "vf": self.aw.irs[t].clock, "supersede": bool(cur)}
        if cur:
            p["old_o"] = cur[0][0]
        out = self.aw.ir_try(t, "AssertFact.OWNED_BY", p, "ops-1")
        return [t], out.code.value

    def do_claim(self):
        t, agent = self.pick(TENANTS), self.pick(("inv-1", "tri-2"))
        out = self.aw.ir_try(t, "ClaimTask", {"agent": agent, "task": "t1", "ttl_ms": 1_000_000}, agent)
        return [t], out.code.value

    def do_complete(self):
        t = self.pick(TENANTS)
        out = self.aw.ir_try(t, "CompleteTask", {"task": "t1", "result": f"done {self.n}"}, "inv-1")
        return [t], out.code.value

    # ── the Executive
    def do_decide(self):
        t = self.pick(TENANTS)
        res = self.aw.executive(t).decide("i1", [], "assure", f"r{self.tag}")
        code = res.outcome.code.value if res.outcome else None
        self.decisions.append((t, res.did, res.state, code, res.envelope["sha"] if res.envelope else None))
        assert self.aw.xws[t].sim_graphs() == [] or res.state == "AWAITING", (res.state, res.error)
        return [t], f"{res.state}:{code}"

    def do_simulate(self):
        t = self.pick(TENANTS)
        before = self.aw.k[t].head()["hash"]
        res = self.aw.executive(t).simulate("i1", [], "assure", f"s{self.tag}")
        assert self.aw.k[t].head()["hash"] == before, "a simulation changed live Head.hash"
        assert self.aw.xws[t].sim_graphs() == []
        return [], res.state

    # ── the optimizer
    def do_optimize(self):
        t = self.pick(TENANTS)
        run = self.aw.optimizer(t).run()
        done = self.aw.reconciler(t).tick()
        return [t], f"{run.state}:{len(done)}"

    # ── federation
    def do_notice(self):
        a, b = self.rng.sample(TENANTS, 2)
        self.aw.notice(a, b, f"n{self.tag}")
        return [a], f"{a}->{b}"

    def do_deliver(self):
        a, b = self.rng.sample(TENANTS, 2)
        report = self.relay.deliver(a, b)
        return [a, b], f"{a}->{b}:{len(report.applied)}:{report.stopped}"

    def do_consume(self):
        consumer, provider = self.rng.sample(TENANTS, 2)
        svc, mine = f"p{self.tag}", f"q{self.tag}"
        self.aw.consume(consumer, provider, svc, mine)
        self.subscriptions.append((consumer, provider, svc))
        return [consumer, provider], f"{consumer}->{provider}:{svc}"

    def do_provider_health(self):
        if not self.subscriptions:
            return [], "none"
        consumer, provider, svc = self.subscriptions[self.rng.randrange(len(self.subscriptions))]
        health = self.pick(HEALTHS)
        self.aw.health(provider, svc, health)
        return [provider], f"{svc}:{health}"

    def do_publish(self):
        t = self.pick(TENANTS)
        outs = self.relay.publish_health(t)
        return [t], ",".join(sorted(o.code.value for o in outs)) or "none"

    def do_sweep(self):
        return list(TENANTS), str(self.relay.sweep())


def fed_entries(aw: AW) -> list[tuple]:
    """The federated log (§17.7) as plain tuples, for determinism comparisons."""
    from gnomon.federation import fedlog

    return [(e.pt, e.lc, e.tenant, e.seq, e.rule, e.agent) for e in
            fedlog.merge({t: aw.db.select_graph(aw.keys[t]) for t in TENANTS})]


def snapshot(aw: AW, machine: Machine) -> dict:
    """Everything a determinism check compares: state hashes, chain heads, decision shas, the trace and the fed log."""
    return {"state": aw.hashes(), "heads": aw.head_hashes(), "decisions": machine.decisions, "trace": machine.trace,
            "fedlog": fed_entries(aw)}
