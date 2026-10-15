"""Incident-response world (A06, shared pytest plugin registered in tests/conftest.py; D-24): agents for every core role, services, a fact to cite, and a replay clock.

Every proposal runs in replay mode at an explicit pt (`IR.clock`), so `now`, lease expiry and probe
diagnosis are deterministic (contract C8). `IR.delta` reports the exact graph change of one proposal,
without the kernel's own log (Head, Rewrite, KeyVersion, CAUSED_BY).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import pytest

EPOCH = 1_791_000_000_000
AGENTS = [
    ("ing-1", "ingest", []), ("inv-1", "investigator", ["ops"]), ("com-1", "comms", ["triage"]),
    ("rsp-1", "responder", ["ops"]), ("sch-1", "scheduler", []), ("dsp-1", "dispatcher", []),
    ("hum-1", "human", []), ("tri-2", "triage", ["ops", "triage"]),
]


@dataclass
class IR:
    k: object
    client: object
    clock: int = EPOCH + 1_000
    pids: itertools.count = field(default_factory=lambda: itertools.count(1))

    def submit(self, rule, p, agent, *, pid=None, at=None, step=10, view_keys=(), read_seq=None):
        self.clock = at if at is not None else self.clock + step
        return self.k.submit(rule, p, pid=pid or f"{rule.split('.')[0].lower()}-{next(self.pids)}", agent=agent,
                             mode="replay", rec_pt=self.clock, rec_lc=0, view_keys=view_keys, read_seq=read_seq)

    def ok(self, rule, p, agent, **kw):
        out = self.submit(rule, p, agent, **kw)
        assert out.code == "APPLIED", (rule, p, out)
        return out

    def guard(self, rule, p, agent, **kw):
        out = self.submit(rule, p, agent, **kw)
        assert out.code == "REJECTED_GUARD", (rule, p, out)
        return out.detail["guard"]

    def delta(self, rule, p, agent, **kw):
        """(outcome, changes) for one proposal; changes as (kind, op, name, prop) tuples."""
        from gnomon.footprint.verify import canonical_state, diff

        before = canonical_state(self.k.graph, self.client)
        out = self.submit(rule, p, agent, **kw)
        changes = diff(before, canonical_state(self.k.graph, self.client))
        return out, {(c.kind, c.op, "/".join(c.name), c.prop) for c in changes}

    def write_keys(self, pid):
        return self.k.read("MATCH (r:Rewrite {id: $pid}) RETURN r.write_keys", {"pid": pid})[0][0]

    def one(self, cypher, **params):
        rows = self.k.read(cypher, params)
        assert len(rows) == 1, rows
        return rows[0]

    def poke(self, cypher, **params):
        """A direct, out-of-band write: only for seeding states the rules cannot reach (guard tests)."""
        self.k.graph.query(cypher, params)

    # ── common flows
    def alert(self, source_id="al-1", service="a", signature="cpu", agent="ing-1", **kw):
        return self.ok("Observe.alert", {"source_id": source_id, "service": service, "signature": signature,
                                         "severity": "high", "observed_pt": self.clock, "payload_sha": "f" * 64},
                       agent, **kw)

    def incident(self, incident="i1", alert="al-1", service="a", signature="cpu", tasks=None, **kw):
        tasks = tasks if tasks is not None else [{"id": "t1", "kind": "investigate", "skill": "ops"}]
        return self.ok("OpenIncident", {"alert": alert, "incident": incident, "service": service,
                                         "signature": signature, "tasks": tasks}, "triage-1", **kw)

    def claim(self, task="t1", agent="inv-1", ttl=1_000, **kw):
        return self.ok("ClaimTask", {"agent": agent, "task": task, "ttl_ms": ttl}, agent, **kw)

    def owned_by_fid(self):
        return self.one("MATCH (:Service {id: 'a'})-[f:OWNED_BY]->() WHERE f.te = $inf RETURN f.fid",
                        inf=9007199254740991)[0]


@pytest.fixture
def ir(genesis, redis_raw) -> IR:
    w = IR(genesis, redis_raw)
    for aid, role, skills in AGENTS:
        w.ok("RegisterAgent", {"op": "register", "id": aid, "role": role, "skills": skills}, "ops-1")
    for sid in ("a", "b"):
        w.ok("RegisterEntity.Service", {"id": sid, "name": sid, "tier": "t1", "criticality": 3, "user_facing": True},
             "ops-1")
    w.ok("RegisterEntity.Team", {"id": "t1", "name": "t1"}, "ops-1")
    w.ok("RegisterEntity.Deploy", {"id": "d1", "service": "a", "version": "1", "verified": True,
                                   "expected_health": "ok"}, "ops-1")
    w.ok("AssertFact.OWNED_BY", {"s": "svc:a", "o": "team:t1", "vf": w.clock, "supersede": False}, "ops-1")
    return w


@pytest.fixture
def opened(ir) -> IR:
    """An open incident i1 on service a with one task t1 (skill ops)."""
    ir.alert()
    ir.incident()
    return ir
