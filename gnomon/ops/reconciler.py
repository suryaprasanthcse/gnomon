"""The Reconciler (master §16.11): the only process that runs DDL on live.

It is stateless and idempotent: on every `tick` it compares each IndexEntry with `CALL db.indexes()` and acts on the
§16.11 table, confirming with M2 (IndexSettled, pid `settle:<key>:<requested_seq>`, so a retry is DUPLICATE). An index
that exists without a log entry, or a logged operational index that is gone, is only reported (K8 audits it too).
Relationship indexes are dropped with the FOR-pattern form (DECISIONS D-6, FACTS F3).
"""

from __future__ import annotations

import time
from collections.abc import Callable

import redis

from gnomon.kernel.outcomes import Outcome
from gnomon.optimizer.config import OptConfig

ENTRIES = """MATCH (x:IndexEntry)
RETURN x.key AS key, x.entity AS entity, x.label AS label, x.prop AS prop, x.state AS state, x.origin AS origin,
       x.requested_seq AS requested_seq
ORDER BY key"""
INDEXES = ("CALL db.indexes() YIELD label, properties, entitytype, status "
           "RETURN label, properties, entitytype, status")
ENTITY = {"NODE": "node", "RELATIONSHIP": "edge"}
IN_LOG = ("requested", "retiring", "operational")


def physical_status(raw: str | None) -> str:
    """absent | building | operational | failed, from the status text of db.indexes()."""
    if raw is None:
        return "absent"
    if raw == "OPERATIONAL":
        return "operational"
    if raw.startswith("FAILED") or raw == "FAILED":
        return "failed"
    return "building"


def action_for(state: str | None, physical: str, genesis: bool = False) -> str:
    """The §16.11 table: what to do for an IndexEntry state (None: no entry) and a physical status."""
    if state == "requested":
        return {"absent": "create", "building": "poll", "operational": "settle_operational",
                "failed": "settle_failed"}[physical]
    if state == "retiring":
        return {"absent": "settle_retired", "building": "poll", "operational": "drop", "failed": "drop"}[physical]
    if state == "operational":
        return "alert_out_of_band_drop" if physical == "absent" else "none"
    if physical != "absent" and not genesis:
        return "alert_out_of_band_create"
    return "none"


def create_statement(entity: str, label: str, prop: str) -> str:
    if entity == "node":
        return f"CREATE INDEX FOR (n:{label}) ON (n.{prop})"
    return f"CREATE INDEX FOR ()-[f:{label}]-() ON (f.{prop})"


def drop_statement(entity: str, label: str, prop: str) -> str:
    if entity == "node":
        return f"DROP INDEX ON :{label}({prop})"
    return f"DROP INDEX FOR ()-[f:{label}]-() ON (f.{prop})"  # D-6


class DDL:
    """Runs one DDL statement on the live graph (a seam so a test can inject a failing executor)."""

    def run(self, graph, statement: str) -> None:
        graph.query(statement)


class Reconciler:
    def __init__(self, db, client, kernel, *, agent: str = "reconciler", mode: str = "live",
                 clock: Callable[[], int] | None = None, config: OptConfig | None = None, ddl: DDL | None = None,
                 monotonic: Callable[[], float] | None = None, sleep: Callable[[float], None] | None = None,
                 poll_ms: int = 50) -> None:
        if mode != "live" and clock is None:
            raise ValueError(f"mode {mode!r} needs a clock")
        self.db, self.client, self.kernel = db, client, kernel
        self.agent, self.mode, self.clock = agent, mode, clock
        self.config = config or OptConfig()
        self.ddl = ddl or DDL()
        self.monotonic = monotonic or (lambda: time.monotonic() * 1000)
        self.sleep = sleep or time.sleep
        self.poll_ms = poll_ms
        self.alerts: list[dict] = []

    # ── what is there
    def physical(self) -> dict[str, str]:
        rows = self.kernel.graph.ro_query(INDEXES).result_set
        return {f"{ENTITY[etype]}:{label}:{prop}": status for label, props, etype, status in rows for prop in props}

    def entries(self) -> list[dict]:
        result = self.kernel.graph.ro_query(ENTRIES)
        names = [h[1] if isinstance(h, (list, tuple)) else h for h in result.header]
        return [dict(zip(names, row)) for row in result.result_set]

    # ── one pass
    def tick(self) -> list[dict]:
        entries = self.entries()
        physical = self.physical()
        done: list[dict] = []
        for e in entries:
            status = physical_status(physical.get(e["key"]))
            action = action_for(e["state"], status, genesis=e["origin"] == "genesis")
            if action != "none":
                done.append(self._act(e, action))
        known = {e["key"] for e in entries}
        for key in sorted(set(physical) - known):
            if physical_status(physical[key]) != "absent":
                done.append(self._alert({"key": key}, "alert_out_of_band_create"))
        return done

    def _act(self, e: dict, action: str) -> dict:
        key = e["key"]
        if action.startswith("alert"):
            return self._alert(e, action)
        started = self.monotonic()
        outcome, detail = {"settle_operational": ("operational", ""), "settle_failed": ("failed", "physical index failed"),
                           "settle_retired": ("retired", "")}.get(action, (None, ""))
        if action == "create":
            outcome, detail = self._ddl(create_statement(e["entity"], e["label"], e["prop"]), key, "operational", started)
        elif action == "drop":
            outcome, detail = self._ddl(drop_statement(e["entity"], e["label"], e["prop"]), key, "absent", started)
        elif action == "poll":
            want = "operational" if e["state"] == "requested" else "absent"
            outcome, detail = self._wait(key, want, started)
        return {"key": key, "action": action, "outcome": self._settle(e, outcome, int(self.monotonic() - started), detail)}

    def _ddl(self, statement: str, key: str, want: str, started: float) -> tuple[str, str]:
        try:
            self.ddl.run(self.kernel.graph, statement)
        except redis.ResponseError as exc:
            return "failed", f"DDL failed: {exc}"
        return self._wait(key, want, started)

    def _wait(self, key: str, want: str, started: float) -> tuple[str, str]:
        """Poll db.indexes() until the index is operational (or absent, for a drop); (outcome, detail)."""
        while True:
            status = physical_status(self.physical().get(key))
            if status == want:
                return ("operational" if want == "operational" else "retired"), ""
            if status == "failed":
                return "failed", "index build failed"
            if self.monotonic() - started >= self.config.ddl_timeout_ms:
                return "failed", f"timeout after {self.config.ddl_timeout_ms} ms"
            self.sleep(self.poll_ms / 1000)

    def _settle(self, e: dict, outcome: str, ddl_ms: int, detail: str) -> Outcome:
        rec_pt = self.clock() if self.mode != "live" and self.clock else None
        return self.kernel.submit(
            "IndexSettled", {"key": e["key"], "outcome": outcome, "ddl_ms": ddl_ms, "detail": detail},
            pid=f"settle:{e['key']}:{e['requested_seq']}", agent=self.agent, mode=self.mode, rec_pt=rec_pt,
            rec_lc=0 if rec_pt is not None else None)

    def _alert(self, e: dict, action: str) -> dict:
        kind = action.removeprefix("alert_")
        self.alerts.append({"kind": kind, "key": e["key"]})
        return {"key": e["key"], "action": action, "outcome": None}
