"""Audit runner (master §21.1, K1–K6) and the WHY-entity provenance query (§21.3)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import redis

from gnomon.audit import CheckResult, k1, k2, k3, k4, k5, k6, k7, k8, k9

CHECKS = ("K1", "K2", "K3", "K4", "K5", "K6")
OPTIONAL_CHECKS = ("K7", "K8")  # selectable by name; part of the default set once a layer with decisions is active

WHY_ENTITY = """MATCH (e:Entity {uid: $uid})
MATCH (w:Rewrite)
WHERE w.id = e.rw
OPTIONAL MATCH chain = (w)-[:CAUSED_BY*1..8]->(:Rewrite)
WITH e, w, collect([n IN nodes(chain) | n.seq]) AS causes
RETURN e.uid AS entity, w.seq AS created_at, w.rule AS rule, w.agent AS agent, causes"""


FEDERATION_CHECKS = (*k9.NAMES, "K1")  # K9a-f over the federation; K1 is each ledger's own hash chain


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
    """Runs the selected checks (default: K1–K6; K7 by name) on graph `key`, in catalogue order."""
    selected = set(CHECKS if checks is None else checks)
    unknown = selected - set(CHECKS) - set(OPTIONAL_CHECKS)
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
        "K7": lambda: [k7.check(graph, db=db, client=client)],
        "K8": lambda: [k8.check(graph)],
    }
    results: dict[str, CheckResult] = {}
    for name in (*CHECKS, *OPTIONAL_CHECKS):
        if name in selected:
            results.update((r.name, r) for r in runs[name]())
    return AuditReport(key, results)


def audit_federation(db, client: redis.Redis, keys: dict[str, str], sys_key: str,
                     checks: Iterable[str] | None = None) -> AuditReport:
    """K9a-K9f over the tenant graphs `keys` (tenant id -> graph key) and the registry graph `sys_key` (master §17.8).

    The default set also re-verifies every ledger's hash chain as `K1@<tenant>` (and `K1@_sys`), so one report says
    that each log is intact, that the logs agree on every message that crossed between them, and that the registry vouches
    for every tenant."""
    selected = set(FEDERATION_CHECKS if checks is None else checks)
    unknown = selected - set(FEDERATION_CHECKS)
    if unknown:
        raise ValueError(f"unknown checks {sorted(unknown)}; known: {', '.join(FEDERATION_CHECKS)}")
    graphs = {tenant: db.select_graph(key) for tenant, key in keys.items()}
    sys_graph = db.select_graph(sys_key)
    results: dict[str, CheckResult] = {r.name: r for r in k9.check(graphs, sys_graph, selected)}
    if "K1" in selected:
        for tenant, graph in {**graphs, "_sys": sys_graph}.items():
            results[f"K1@{tenant}"] = CheckResult(f"K1@{tenant}", k1.check(graph).findings)
    return AuditReport("federation", results)


def why_entity(graph, uid: str) -> dict | None:
    """The rewrite that last wrote the entity, with its causal chains (lists of seqs, newest first)."""
    rows = graph.ro_query(WHY_ENTITY, {"uid": uid}).result_set
    if not rows:
        return None
    entity, created_at, rule, agent, causes = rows[0]
    return {"entity": entity, "created_at": created_at, "rule": rule, "agent": agent,
            "causes": sorted(c for c in causes if c)}
