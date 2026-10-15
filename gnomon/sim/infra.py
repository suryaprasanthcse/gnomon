"""Synthetic infrastructure (master §11.4): a deterministic, seeded simulator outside the graph.

It is the real world the agents act on. Each service has a running deploy and a mode; its actual health is a
function of the running deploy's true quality, the health of its hard dependencies and its mode. Actions are
idempotent by pid. Every change in actual health is queued as an `Observe.health` event with a seeded latency,
and the bad deploy raises one alert. The graph's belief (`expected_health` of the bad deploy is `ok`) may
disagree with this truth until the investigation corrects it (§28, limit 1).

Chaos hooks: `drop_next` (failed deliveries), `duplicate_next` (a delivery reaches the infrastructure twice).
"""

from __future__ import annotations

import hashlib
import random

ACTIONS = ("rollback", "isolate", "failover", "restore", "page", "post_update")
LATENCY_MS = (1_000, 30_000)
DELIVERY_KINDS = ("post_update",)


class Infra:
    def __init__(self, seed: int, world=None) -> None:
        self.seed = seed
        self.world = world
        self._rng = random.Random(seed)
        self._receipts: dict[str, dict] = {}
        self._applied: dict[str, int] = {}
        self._deliveries: dict[str, dict] = {}
        self._drop = self._duplicate = 0
        self._events: list[dict] = []
        self._alerts: list[dict] = []
        self._counter: dict[str, int] = {}
        self._reported: dict[str, str] = {}
        self._mode: dict[str, str] = {}
        self._running: dict[str, str] = {}
        self._history: dict[str, list[str]] = {}
        self._bad: set[str] = set()
        self._hard_deps: dict[str, list[str]] = {}
        if world is not None:
            self._load(world)

    def _load(self, world) -> None:
        by_service: dict[str, list[dict]] = {}
        for d in sorted(world.deploys, key=lambda d: (d["vf"], d["id"])):
            by_service.setdefault(d["service"], []).append(d)
            if d["bad"]:
                self._bad.add(d["id"])
        for s in world.services:
            sid = s["id"]
            self._history[sid] = [d["id"] for d in by_service.get(sid, [])]
            self._running[sid] = self._history[sid][-1] if self._history[sid] else ""
            self._mode[sid] = "normal"
            self._reported[sid] = "ok"
            self._counter[sid] = 0
            self._hard_deps[sid] = []
        for a, b, hard in world.edges:
            if hard:
                self._hard_deps[a].append(b)

    # ── truth
    def running_deploy(self, service: str) -> str:
        return self._running[service]

    def health(self, service: str) -> str:
        mode = self._mode[service]
        if mode == "isolated" or self._running[service] in self._bad:
            return "down"
        if mode == "failed_over":
            return "degraded" if any(not r["healthy"] for r in self.world.replicas if r["service"] == service) else "ok"
        if any(self._own(dep) == "down" for dep in self._hard_deps[service]):
            return "degraded"
        return "ok"

    def _own(self, service: str) -> str:
        """Health from the service's own deploy and mode, before dependencies (a down dependency degrades once)."""
        if self._mode[service] == "isolated" or self._running[service] in self._bad:
            return "down"
        return "ok"

    # ── events
    def start(self, now: int) -> None:
        self._recompute(now)
        for service in sorted(self._running):
            if self._running[service] in self._bad:
                due = now + self._rng.randint(*LATENCY_MS)
                digest = hashlib.sha256(f"{self.seed}:{service}:{self._running[service]}".encode()).hexdigest()
                self._alerts.append({"source_id": f"al:{service}:1", "service": service,
                                     "signature": "latency_high", "severity": "high", "observed_pt": due,
                                     "payload_sha": digest})

    def _recompute(self, now: int) -> None:
        for service in sorted(self._running):
            health = self.health(service)
            if health != self._reported[service]:
                self._reported[service] = health
                self._counter[service] += 1
                due = now + self._rng.randint(*LATENCY_MS)
                self._events.append({"source_id": f"hl:{service}:{self._counter[service]}", "service": service,
                                     "health": health, "observed_pt": due})

    def due_events(self, now: int) -> list[dict]:
        due = sorted((e for e in self._events if e["observed_pt"] <= now), key=lambda e: (e["observed_pt"], e["source_id"]))
        self._events = [e for e in self._events if e["observed_pt"] > now]
        return due

    def due_alerts(self, now: int) -> list[dict]:
        due = sorted((a for a in self._alerts if a["observed_pt"] <= now), key=lambda a: (a["observed_pt"], a["source_id"]))
        self._alerts = [a for a in self._alerts if a["observed_pt"] > now]
        return due

    # ── actions (idempotent by pid)
    def act(self, pid: str, action: str, service: str, *, now: int) -> dict:
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}; known: {', '.join(ACTIONS)}")
        if pid in self._receipts:
            return dict(self._receipts[pid])
        if action == "rollback":
            history = self._history[service]
            at = history.index(self._running[service])
            if at > 0:
                self._running[service] = history[at - 1]
        elif action == "isolate":
            self._mode[service] = "isolated"
        elif action == "failover":
            self._mode[service] = "failed_over"
        elif action == "restore":
            self._mode[service] = "normal"
        self._applied[pid] = 1
        receipt = {"pid": pid, "action": action, "service": service, "outcome": "applied"}
        self._receipts[pid] = receipt
        self._recompute(now)
        return dict(receipt)

    # ── outbox delivery (idempotent by pid)
    def drop_next(self, n: int) -> None:
        self._drop = n

    def duplicate_next(self, n: int) -> None:
        self._duplicate = n

    def deliver(self, row: dict) -> dict:
        pid = row["pid"]
        if pid in self._deliveries:
            return dict(self._deliveries[pid])
        if self._drop > 0:
            self._drop -= 1
            return {"pid": pid, "outcome": "failed", "attempt": row.get("attempts", 0)}
        if row.get("kind") not in DELIVERY_KINDS:
            return {"pid": pid, "outcome": "failed", "attempt": row.get("attempts", 0)}
        times = 2 if self._duplicate > 0 else 1
        self._duplicate = max(0, self._duplicate - 1)
        for _ in range(times):
            self._applied.setdefault(pid, 0)
            if pid not in self._deliveries:
                self._applied[pid] = 1  # the second arrival finds the pid applied and changes nothing
                self._deliveries[pid] = {"pid": pid, "outcome": "delivered", "attempt": row.get("attempts", 0)}
        return dict(self._deliveries[pid])

    def apply_count(self, pid: str) -> int:
        return self._applied.get(pid, 0)
