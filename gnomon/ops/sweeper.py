"""Sweeper (master §11.1): surfaces what the loops cannot fix by themselves, read-only.

Dead outbox rows are surfaced to operators (§11.1), pending rows that already failed are listed as stuck, and
leases past their time that nothing has closed yet are listed so a stopped scheduler is visible. Given a Redis client
(T-F, §19.2) it also deletes the as-of scratch graphs older than ASOF_TTL_MS, the one thing it writes.
"""

from __future__ import annotations

from collections.abc import Callable

from gnomon.temporal import asof


class Sweeper:
    def __init__(self, kernel, clock: Callable[[], int], client=None) -> None:
        self.kernel, self.clock, self.client = kernel, clock, client

    def sweep(self) -> dict:
        read = self.kernel.read
        found = {
            "dead_outbox": [r[0] for r in read("MATCH (o:Outbox {state: 'dead'}) RETURN o.pid ORDER BY o.pid")],
            "stuck_pending": [r[0] for r in read("MATCH (o:Outbox {state: 'pending'}) WHERE o.attempts > 0 "
                                                 "RETURN o.pid ORDER BY o.pid")],
            "expired_leases": [r[0] for r in read(
                "MATCH (:Agent)-[l:HOLDS]->(t:Task) WHERE l.closed_seq IS NULL AND l.until_pt <= $wall "
                "RETURN DISTINCT t.id ORDER BY t.id", {"wall": self.clock()})],
        }
        if self.client is not None:
            found["asof_deleted"] = asof.sweep(self.client, self.kernel.graph.name, self.clock())
        return found
