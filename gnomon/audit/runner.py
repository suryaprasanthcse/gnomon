"""Audit runner (master §21.1, K1–K6) and the WHY-entity provenance query (§21.3)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import redis

from gnomon.audit import CheckResult, k1, k2, k3, k4, k5, k6

CHECKS = ("K1", "K2", "K3", "K4", "K5", "K6")

WHY_ENTITY = """MATCH (e:Entity {uid: $uid})
MATCH (w:Rewrite)
WHERE w.id = e.rw
OPTIONAL MATCH chain = (w)-[:CAUSED_BY*1..8]->(:Rewrite)
WITH e, w, collect([n IN nodes(chain) | n.seq]) AS causes
RETURN e.uid AS entity, w.seq AS created_at, w.rule AS rule, w.agent AS agent, causes"""


@dataclass(frozen=True)
class AuditReport:
    graph: str
    results: dict[str, CheckResult]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results.values())

    @property
    def failed(self) -> list[str]:
        return [name for name, r in self.results.items() if not r.ok]

    def as_dict(self) -> dict:
        return {"graph": self.graph, "ok": self.ok, "failed": self.failed,
                "checks": {name: r.as_dict() for name, r in self.results.items()}}


def audit(db, client: redis.Redis, key: str, checks: Iterable[str] | None = None) -> AuditReport:
    """Runs the selected checks (default: K1–K6) on graph `key`, in catalogue order."""
    selected = set(CHECKS if checks is None else checks)
    unknown = selected - set(CHECKS)
    if unknown:
        raise ValueError(f"unknown checks {sorted(unknown)}; known: {', '.join(CHECKS)}")
    graph = db.select_graph(key)
    runs = {
        "K1": lambda: [k1.check(graph)],
        "K2": lambda: [k2.check(graph)],
        "K3": lambda: [k3.check(graph, client)],
        "K4": lambda: [k4.check(db, client, key)],
        "K5": lambda: k5.check(graph),
        "K6": lambda: [k6.check(graph)],
    }
    results: dict[str, CheckResult] = {}
    for name in CHECKS:
        if name in selected:
            results.update((r.name, r) for r in runs[name]())
    return AuditReport(key, results)


def why_entity(graph, uid: str) -> dict | None:
    """The rewrite that last wrote the entity, with its causal chains (lists of seqs, newest first)."""
    rows = graph.ro_query(WHY_ENTITY, {"uid": uid}).result_set
    if not rows:
        return None
    entity, created_at, rule, agent, causes = rows[0]
    return {"entity": entity, "created_at": created_at, "rule": rule, "agent": agent,
            "causes": sorted(c for c in causes if c)}
