"""Lease scheduler (master §11.3): propose R8 for every lease that wall time says has expired.

The wall clock only selects candidates. ExpireLease's guard then compares the lease with the rewrite's own `now`,
which is never earlier than the wall time (and never earlier than Head), so a lease is closed only when the log
itself says it expired. The pid `exp:<task>:<from_seq>` is deterministic per lease, so a repeat is a DUPLICATE.
"""

from __future__ import annotations

from collections.abc import Callable

from gnomon.ops.dispatcher import proposal_time

EXPIRED = ("MATCH (:Agent)-[l:HOLDS]->(t:Task) WHERE l.closed_seq IS NULL AND l.until_pt <= $wall "
           "RETURN t.id, l.from_seq ORDER BY t.id, l.from_seq")


class Scheduler:
    def __init__(self, kernel, clock: Callable[[], int], *, agent: str = "sch-1", mode: str = "replay") -> None:
        self.kernel, self.clock, self.agent, self.mode = kernel, clock, agent, mode

    def tick(self) -> list[tuple[str, object]]:
        wall = self.clock()
        out = []
        for task, from_seq in self.kernel.read(EXPIRED, {"wall": wall}):
            pid = f"exp:{task}:{from_seq}"
            now = max(wall, proposal_time(self.kernel, self.clock))
            out.append((pid, self.kernel.submit(
                "ExpireLease", {"task": task}, pid=pid, agent=self.agent, mode=self.mode,
                rec_pt=now if self.mode != "live" else None, rec_lc=0 if self.mode != "live" else None)))
        return out
