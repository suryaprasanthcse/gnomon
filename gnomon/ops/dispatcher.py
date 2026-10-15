"""Outbox dispatcher (master §11.2): deliver pending outbox rows to the infrastructure and record receipts.

Delivery uses the row's `pid` as the idempotency key, so the infrastructure applies each pid at most once. The
receipt proposal carries `source_id = rcpt:<pid>:<attempts>`, which is deterministic, so a repeated receipt is
refused by the Observe.receipt guard instead of being applied twice.
"""

from __future__ import annotations

from collections.abc import Callable

PENDING = ("MATCH (o:Outbox {state: 'pending'}) RETURN o.pid, o.kind, o.target, o.to, o.incident, o.payload, "
           "o.attempts ORDER BY o.pid LIMIT $batch")
COLUMNS = ("pid", "kind", "target", "to", "incident", "payload", "attempts")


def proposal_time(kernel, clock: Callable[[], int]) -> int:
    """The recorded time for a service proposal: the wall clock, and strictly after Head so the HLC advances."""
    return max(clock(), kernel.head().get("pt", 0) + 1)


class Dispatcher:
    def __init__(self, kernel, infra, clock: Callable[[], int], *, agent: str = "dsp-1", mode: str = "replay",
                 batch: int = 50) -> None:
        self.kernel, self.infra, self.clock = kernel, infra, clock
        self.agent, self.mode, self.batch = agent, mode, batch

    def tick(self) -> list[dict]:
        done = []
        for values in self.kernel.read(PENDING, {"batch": self.batch}):
            row = dict(zip(COLUMNS, values))
            delivery = self.infra.deliver(row)
            source_id = f"rcpt:{row['pid']}:{row['attempts']}"
            now = proposal_time(self.kernel, self.clock)
            receipt = self.kernel.submit(
                "Observe.receipt",
                {"source_id": source_id, "outbox": row["pid"], "outcome": delivery["outcome"], "observed_pt": now},
                pid=f"dsp:{row['pid']}:{row['attempts']}", agent=self.agent, mode=self.mode,
                rec_pt=now if self.mode != "live" else None, rec_lc=0 if self.mode != "live" else None)
            done.append({"pid": row["pid"], "attempts": row["attempts"], "delivery": delivery, "receipt": receipt})
        return done
