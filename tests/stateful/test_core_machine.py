"""Core state machine (RUNBOOK_A A07): random runs of agent-callable Core rules keep K5 after every step, and
at teardown K1–K6 pass and the replayed state hash equals the live one.
"""

from __future__ import annotations

import uuid

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule, run_state_machine_as_test

from tests.support.ir_world import AGENTS, IR

pytestmark = [pytest.mark.integration, pytest.mark.stateful, pytest.mark.gate_g3]

INF = 9007199254740991
INCIDENTS = ["i1", "i2"]
SERVICES = ["a", "b"]
WORKERS = ["inv-1", "rsp-1", "tri-2"]


class CoreMachine(RuleBasedStateMachine):
    env: tuple = ()

    @initialize()
    def setup(self):
        from gnomon.kernel.service import KernelHandle

        db, client, golden, registry = self.env
        self.client = client
        self.key = f"t:test-{uuid.uuid4().hex[:8]}"
        client.execute_command("GRAPH.COPY", golden, self.key)
        self.db = db
        self.ir = IR(KernelHandle(db.select_graph(self.key), registry), client)
        for aid, role, skills in AGENTS:
            self.ir.ok("RegisterAgent", {"op": "register", "id": aid, "role": role, "skills": skills}, "ops-1")
        for sid in SERVICES:
            self.ir.ok("RegisterEntity.Service", {"id": sid, "name": sid, "tier": "t1", "criticality": 3,
                                                  "user_facing": True}, "ops-1")
        self.ir.ok("RegisterEntity.Team", {"id": "t1", "name": "t1"}, "ops-1")
        self.ir.ok("RegisterEntity.Deploy", {"id": "d1", "service": "a", "version": "1", "verified": True,
                                             "expected_health": "ok"}, "ops-1")
        self.ir.ok("AssertFact.OWNED_BY", {"s": "svc:a", "o": "team:t1", "vf": self.ir.clock, "supersede": False},
                   "ops-1")
        self.n = 0

    def go(self, rule_name, p, agent, step=10):
        self.n += 1
        out = self.ir.submit(rule_name, p, agent, pid=f"sm-{self.n}", step=step)
        assert out.code != "KERNEL_ERROR", (rule_name, p, out)
        return out

    def current_fid(self, rel, s, o=None):
        q = f"MATCH (:Entity {{uid: $s}})-[f:{rel}]->(o:Entity) WHERE f.te = $inf AND f.vt = $inf " \
            "AND ($o IS NULL OR o.uid = $o) RETURN f.fid ORDER BY f.fid LIMIT 1"
        rows = self.ir.k.read(q, {"s": s, "o": o, "inf": INF})
        return rows[0][0] if rows else "none#0"

    @rule(n=st.integers(1, 3), service=st.sampled_from(SERVICES))
    def observe_alert(self, n, service):
        self.go("Observe.alert", {"source_id": f"al-{n}-{self.n}", "service": service, "signature": "cpu",
                                  "severity": "high", "observed_pt": self.ir.clock, "payload_sha": "f" * 64}, "ing-1")

    @rule(service=st.sampled_from(SERVICES), health=st.sampled_from(["ok", "down"]))
    def observe_health(self, service, health):
        self.go("Observe.health", {"source_id": f"h-{self.n}", "service": service, "health": health,
                                   "observed_pt": self.ir.clock}, "ing-1")

    @rule(incident=st.sampled_from(INCIDENTS), service=st.sampled_from(SERVICES))
    def open_incident(self, incident, service):
        alert = f"al-x-{self.n}"
        self.go("Observe.alert", {"source_id": alert, "service": service, "signature": incident,
                                  "severity": "high", "observed_pt": self.ir.clock, "payload_sha": "f" * 64}, "ing-1")
        self.go("OpenIncident", {"alert": alert, "incident": incident, "service": service, "signature": incident,
                                 "tasks": [{"id": f"{incident}-t", "kind": "investigate", "skill": "ops"}]},
                "triage-1")

    @rule(incident=st.sampled_from(INCIDENTS), agent=st.sampled_from(WORKERS), ttl=st.sampled_from([500, 50_000]))
    def claim(self, incident, agent, ttl):
        self.go("ClaimTask", {"agent": agent, "task": f"{incident}-t", "ttl_ms": ttl}, agent)

    def holder(self, task, fallback):
        """The agent holding the task's open lease (so completion and handoff can apply), else `fallback`."""
        rows = self.ir.k.read("MATCH (a:Agent)-[l:HOLDS]->(:Task {id: $t}) WHERE l.closed_seq IS NULL "
                              "RETURN a.id ORDER BY a.id LIMIT 1", {"t": task})
        return rows[0][0] if rows else fallback

    @rule(incident=st.sampled_from(INCIDENTS), agent=st.sampled_from(WORKERS), as_holder=st.booleans())
    def complete(self, incident, agent, as_holder):
        task = f"{incident}-t"
        self.go("CompleteTask", {"task": task, "result": "done"}, self.holder(task, agent) if as_holder else agent)

    @rule(incident=st.sampled_from(INCIDENTS), agent=st.sampled_from(WORKERS), to=st.sampled_from(WORKERS),
          as_holder=st.booleans())
    def handoff(self, incident, agent, to, as_holder):
        task = f"{incident}-t"
        self.go("Handoff", {"task": task, "to": to, "ttl_ms": 50_000}, self.holder(task, agent) if as_holder else agent)

    @rule(incident=st.sampled_from(INCIDENTS))
    def expire(self, incident):
        self.go("ExpireLease", {"task": f"{incident}-t"}, "sch-1", step=1_000)

    @rule(incident=st.sampled_from(INCIDENTS))
    def post_update(self, incident):
        self.go("PostUpdate", {"incident": incident, "message_id": f"m-{self.n}", "body": "update",
                               "cites": [{"s": "svc:a", "fid": self.current_fid("OWNED_BY", "svc:a")}]}, "com-1")

    @rule(incident=st.sampled_from(INCIDENTS), cause=st.sampled_from(["dep:d1", "svc:a"]))
    def propose_cause(self, incident, cause):
        self.go("ProposeCause", {"incident": incident, "cause": cause, "confidence": 60, "cites": []}, "inv-1")

    @rule(incident=st.sampled_from(INCIDENTS))
    def resolve(self, incident):
        self.go("ResolveIncident", {"incident": incident}, "hum-1")

    @rule(s=st.sampled_from(SERVICES), o=st.sampled_from(SERVICES), hard=st.booleans())
    def assert_dependency(self, s, o, hard):
        self.go("AssertFact.DEPENDS_ON", {"s": f"svc:{s}", "o": f"svc:{o}", "vf": self.ir.clock, "supersede": False,
                                          "hard": hard}, "ops-1")

    @rule(s=st.sampled_from(SERVICES), o=st.sampled_from(SERVICES), mode=st.sampled_from(["void", "end"]))
    def retract_dependency(self, s, o, mode):
        p = {"s": f"svc:{s}", "o": f"svc:{o}", "fid": self.current_fid("DEPENDS_ON", f"svc:{s}", f"svc:{o}"),
             "mode": mode}
        if mode == "end":
            p["vt_end"] = self.ir.clock
        self.go("RetractFact.DEPENDS_ON", p, "ops-1")

    @rule()
    def receipt(self):
        rows = self.ir.k.read("MATCH (o:Outbox) WHERE o.state = 'pending' RETURN o.pid ORDER BY o.pid LIMIT 1")
        if rows:
            self.go("Observe.receipt", {"source_id": f"rc-{self.n}", "outbox": rows[0][0], "outcome": "delivered",
                                        "observed_pt": self.ir.clock}, "dsp-1")

    @invariant()
    def k5_holds(self):
        from gnomon.audit import k5

        if getattr(self, "ir", None) is None:
            return
        failed = [r.as_dict() for r in k5.check(self.ir.k.graph) if not r.ok]
        assert failed == [], failed

    def teardown(self):
        from gnomon.audit.runner import audit

        if getattr(self, "ir", None) is None:
            return
        try:
            report = audit(self.db, self.client, self.key)
            assert report.ok, report.as_dict()
        finally:
            for name in self.client.execute_command("GRAPH.LIST"):
                if name == self.key or name.startswith(self.key + ":"):
                    self.client.execute_command("GRAPH.DELETE", name)


def test_core_machine(db, redis_raw, golden_genesis, genesis_config):
    machine = type("Machine", (CoreMachine,), {"env": (db, redis_raw, golden_genesis, genesis_config["registry"])})
    run_state_machine_as_test(machine, settings=settings(
        max_examples=10, stateful_step_count=30, deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture]))
