"""Bootstrap DDL (master §3.5): the base index and constraint catalogue, created before R0.

DDL cannot share a query with writes, so `apply` runs the statements, then polls db.indexes() and
db.constraints() until every entry is OPERATIONAL (platform facts S6, F4: a constraint needs its
index first and is created asynchronously). R0 records the same catalogue as IndexEntry and
ConstraintEntry rows. Index keys are "<entity>:<label>:<prop>" with entity node | edge (§16.6);
constraint keys are "<kind>:<entity>:<label>:<prop,prop…>".
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import redis

NODE, EDGE = "node", "edge"
_ENTITY = {"NODE": NODE, "RELATIONSHIP": EDGE}


@dataclass(frozen=True)
class IndexSpec:
    entity: str
    label: str
    prop: str

    @property
    def key(self) -> str:
        return f"{self.entity}:{self.label}:{self.prop}"

    def statement(self) -> str:
        if self.entity == NODE:
            return f"CREATE INDEX FOR (n:{self.label}) ON (n.{self.prop})"
        return f"CREATE INDEX FOR ()-[f:{self.label}]-() ON (f.{self.prop})"

    def as_param(self) -> dict:
        return {"key": self.key, "entity": self.entity, "label": self.label, "prop": self.prop}


@dataclass(frozen=True)
class ConstraintSpec:
    kind: str
    entity: str
    label: str
    props: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.entity}:{self.label}:{','.join(self.props)}"

    def command(self, graph_key: str) -> list[str]:
        entity = "NODE" if self.entity == NODE else "RELATIONSHIP"
        return ["GRAPH.CONSTRAINT", "CREATE", graph_key, self.kind.upper(), entity, self.label,
                "PROPERTIES", str(len(self.props)), *self.props]

    def as_param(self) -> dict:
        return {"key": self.key, "kind": self.kind, "entity": self.entity, "label": self.label,
                "props": list(self.props)}


_NODE_INDEXES = [
    ("Head", "tenant"), ("Rule", "name"), ("Rule", "version"), ("Rule", "sha"), ("KeyVersion", "key"),
    ("Rewrite", "id"), ("Rewrite", "seq"), ("FactType", "rel"), ("Entity", "uid"), ("Agent", "id"),
    ("Skill", "id"), ("Team", "id"), ("Service", "id"), ("Deploy", "id"), ("Event", "source_id"), ("Alert", "id"),
    ("Incident", "id"), ("Incident", "service"), ("Task", "id"), ("Message", "id"), ("Outbox", "pid"),
    ("Outbox", "state"), ("IndexEntry", "key"), ("ConstraintEntry", "key"),
    # [T-A]
    ("Scorer", "name"), ("Scorer", "sha"), ("Approval", "aid"), ("Decision", "id"), ("Candidate", "cid"),
    ("Evidence", "eid"),
    # [T-D]
    ("Channel", "key"), ("XMsg", "id"), ("XMsg", "state"), ("ExternalService", "remote_id"),
]
FACT_RELS = ["OWNED_BY", "DEPENDS_ON", "DEPLOYED", "REPLICA_OF", "MEMBER_OF", "ROOT_CAUSE", "CONSUMES"]
PROJECTIONS = ["DEP_NOW", "OWNER_NOW", "CONSUMES_NOW"]
_UNIQUE_NODE = [
    ("Head", ("tenant",)), ("Rule", ("sha",)), ("Rule", ("name", "version")), ("KeyVersion", ("key",)),
    ("Rewrite", ("id",)), ("Rewrite", ("seq",)), ("FactType", ("rel",)), ("Entity", ("uid",)),
    ("Event", ("source_id",)), ("Outbox", ("pid",)), ("IndexEntry", ("key",)), ("ConstraintEntry", ("key",)),
    ("Scorer", ("sha",)), ("Approval", ("aid",)), ("Decision", ("id",)), ("Candidate", ("cid",)),
    ("Evidence", ("eid",)), ("Channel", ("key",)), ("XMsg", ("id",)),
]

BASE_INDEXES: tuple[IndexSpec, ...] = (
    *(IndexSpec(NODE, label, prop) for label, prop in _NODE_INDEXES),
    *(IndexSpec(EDGE, rel, prop) for rel in FACT_RELS for prop in ("fid", "te")),
    *(IndexSpec(EDGE, rel, "fid") for rel in PROJECTIONS),
)
BASE_CONSTRAINTS: tuple[ConstraintSpec, ...] = (
    *(ConstraintSpec("unique", NODE, label, props) for label, props in _UNIQUE_NODE),
    ConstraintSpec("mandatory", NODE, "Rewrite", ("seq", "rule_sha")),
    *(ConstraintSpec("unique", EDGE, rel, ("fid",)) for rel in FACT_RELS),
)


def _indexes(graph) -> dict[str, str]:
    rows = graph.ro_query("CALL db.indexes() YIELD label, properties, entitytype, status "
                          "RETURN label, properties, entitytype, status").result_set
    return {f"{_ENTITY[etype]}:{label}:{prop}": status for label, props, etype, status in rows for prop in props}


def _constraints(graph) -> dict[str, str]:
    rows = graph.ro_query("CALL db.constraints() YIELD type, label, properties, entitytype, status "
                          "RETURN type, label, properties, entitytype, status").result_set
    return {f"{ctype.lower()}:{_ENTITY[etype]}:{label}:{','.join(props)}": status
            for ctype, label, props, etype, status in rows}


def physical_catalogue(graph) -> tuple[set[str], set[str]]:
    """Keys of the indexes and constraints the server reports as OPERATIONAL."""
    indexes = {k for k, status in _indexes(graph).items() if status == "OPERATIONAL"}
    constraints = {k for k, status in _constraints(graph).items() if status == "OPERATIONAL"}
    return indexes, constraints


def _wait(read, keys: set[str], what: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        statuses = read()
        failed = sorted(k for k in keys if statuses.get(k) == "FAILED")
        if failed:
            raise RuntimeError(f"{what} failed to build: {failed}")
        pending = sorted(k for k in keys if statuses.get(k) != "OPERATIONAL")
        if not pending:
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"{what} not operational after {timeout_s:.0f}s: {pending}")
        time.sleep(0.05)


def apply(graph, client: redis.Redis, timeout_s: float = 120.0) -> None:
    """Create every missing base index, then every missing constraint, waiting until all are operational."""
    present = _indexes(graph) if client.exists(graph.name) else {}  # a new tenant has no graph key yet
    for index in BASE_INDEXES:
        if index.key not in present:
            graph.query(index.statement())
    _wait(lambda: _indexes(graph), {i.key for i in BASE_INDEXES}, "indexes", timeout_s)
    present = _constraints(graph)
    for constraint in BASE_CONSTRAINTS:
        if constraint.key not in present:
            client.execute_command(*constraint.command(graph.name))
    _wait(lambda: _constraints(graph), {c.key for c in BASE_CONSTRAINTS}, "constraints", timeout_s)
