"""Fact-test world (A04): entities registered through M7, and helpers to assert, retract and read versions."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import pytest

INF = 9007199254740991
EPOCH = 1_791_000_000_000  # genesis pt of every test tenant; any later vf up to the wall clock is "not future"
FUTURE = 4_102_444_800_000  # 2100-01-01


@dataclass
class World:
    k: object  # KernelHandle
    _pids: itertools.count

    def pid(self, prefix: str) -> str:
        return f"{prefix}-{next(self._pids)}"

    def entity(self, label: str, **p):
        out = self.k.submit(f"RegisterEntity.{label}", p, pid=self.pid(f"reg-{label}"), agent="ops-1")
        assert out.code == "APPLIED", out
        return out

    def assert_fact(self, rel, s, o, vf, *, supersede=False, old_o=None, agent="ops-1", pid=None, **extra):
        p = {"s": s, "o": o, "vf": vf, "supersede": supersede, **extra}
        if old_o is not None:
            p["old_o"] = old_o
        return self.k.submit(f"AssertFact.{rel}", p, pid=pid or self.pid("af"), agent=agent)

    def retract(self, rel, s, o, fid, mode, vt_end=None, *, pid=None):
        p = {"s": s, "o": o, "fid": fid, "mode": mode}
        if vt_end is not None:
            p["vt_end"] = vt_end
        return self.k.submit(f"RetractFact.{rel}", p, pid=pid or self.pid("rf"), agent="ops-1")

    def versions(self, rel, s=None):
        """Every version (current or not) of `rel`, optionally from subject uid `s`, ordered by fid."""
        where = "WHERE a.uid = $s " if s else ""
        return [dict(zip(("s", "o", "fid", "prev_fid", "vf", "vt", "ts", "te", "tf", "tt", "rw", "hard"), row))
                for row in self.k.read(
                    f"MATCH (a)-[f:{rel}]->(b) {where}RETURN a.uid, b.uid, f.fid, f.prev_fid, f.vf, f.vt, f.ts, "
                    f"f.te, f.tf, f.tt, f.rw, f.hard ORDER BY f.fid", {"s": s})]

    def current(self, rel, s=None):
        return [v for v in self.versions(rel, s) if v["te"] == INF]

    def projection(self, proj):
        return self.k.read(f"MATCH (a)-[p:{proj}]->(b) RETURN a.uid, b.uid, p.fid, p.hard ORDER BY p.fid")

    def write_keys(self, pid):
        return self.k.read("MATCH (r:Rewrite {id: $pid}) RETURN r.write_keys", {"pid": pid})[0][0]

    def head(self):
        return self.k.head()


@pytest.fixture
def world(genesis) -> World:
    w = World(genesis, itertools.count(1))
    for sid in ("a", "b", "c"):
        w.entity("Service", id=sid, name=f"Service {sid}", tier="t1", criticality=3, user_facing=True)
    for tid in ("t1", "t2", "t3"):
        w.entity("Team", id=tid, name=f"Team {tid}")
    w.entity("ExternalService", id="x1", tenant="acme", remote_id="svc-9", contract_criticality=2)
    w.entity("Deploy", id="d1", service="a", version="1.0", verified=True, expected_health="ok")
    w.entity("Deploy", id="d2", service="b", version="2.0", verified=False, expected_health="degraded")
    return w
