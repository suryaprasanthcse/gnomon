"""Sweeper (master §11.1): surfaces what the loops cannot fix by themselves, read-only.

Dead outbox rows are surfaced to operators (§11.1), pending rows that already failed are listed as stuck, and
leases past their time that nothing has closed yet are listed so a stopped scheduler is visible.
"""

from __future__ import annotations

from collections.abc import Callable


class Sweeper:
    def __init__(self, kernel, clock: Callable[[], int]) -> None:
        self.kernel, self.clock = kernel, clock

    def sweep(self) -> dict:
        read = self.kernel.read
        return {
            "dead_outbox": [r[0] for r in read("MATCH (o:Outbox {state: 'dead'}) RETURN o.pid ORDER BY o.pid")],
            "stuck_pending": [r[0] for r in read("MATCH (o:Outbox {state: 'pending'}) WHERE o.attempts > 0 "
                                                 "RETURN o.pid ORDER BY o.pid")],
            "expired_leases": [r[0] for r in read(
                "MATCH (:Agent)-[l:HOLDS]->(t:Task) WHERE l.closed_seq IS NULL AND l.until_pt <= $wall "
                "RETURN DISTINCT t.id ORDER BY t.id", {"wall": self.clock()})],
        }
