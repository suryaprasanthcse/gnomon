"""The branch manager (master §15.2): copies of the live graph that simulations apply to and the scorer reads.

`GRAPH.COPY` is a consistent snapshot as of the command, so the base is the world at one `Head.seq`; every branch is a
copy of the base and shares that `read_seq`. Branch graphs are named `<live>:sim:<did>:<attempt>:<base|k>` and are
disposable: nothing here ever writes the live graph.
"""

from __future__ import annotations

from dataclasses import dataclass

VERIFY_BASE = """MATCH (h:Head)
OPTIONAL MATCH (last:Rewrite) WHERE last.seq = h.seq
WITH h, last
OPTIONAL MATCH (r:Rewrite)
WITH h, last, count(r) AS n
RETURN h.seq AS seq, h.pt AS pt, h.lc AS lc, n = h.seq AS gapless, last IS NOT NULL AND last.hash = h.hash AS linked"""
SNAPSHOT_TRIES = 2


class SnapshotError(RuntimeError):
    """A base snapshot that fails VERIFY-BASE twice."""


@dataclass(frozen=True)
class Base:
    key: str
    seq: int
    pt: int
    lc: int
    gapless: bool
    linked: bool

    @property
    def ok(self) -> bool:
        return self.gapless and self.linked

    @property
    def prefix(self) -> str:
        return self.key[: -len("base")]


def sim_name(live: str, did: str, attempt: int, part: str | int) -> str:
    return f"{live}:sim:{did}:{attempt}:{part}"


class BranchManager:
    def __init__(self, db, client, live: str) -> None:
        self.db, self.client, self.live = db, client, live

    def verify(self, key: str) -> Base:
        seq, pt, lc, gapless, linked = self.db.select_graph(key).ro_query(VERIFY_BASE).result_set[0]
        return Base(key, seq, pt, lc, bool(gapless), bool(linked))

    def snapshot(self, did: str, attempt: int, source: str | None = None) -> Base:
        """GRAPH.COPY of the live graph (or `source`), verified; a failed verification retries once, then raises."""
        key = sim_name(self.live, did, attempt, "base")
        for _ in range(SNAPSHOT_TRIES):
            self.retire([key])
            self.client.execute_command("GRAPH.COPY", source or self.live, key)
            base = self.verify(key)
            if base.ok:
                return base
            self.retire([key])
        raise SnapshotError(f"base snapshot {key} failed VERIFY-BASE twice")

    def branch(self, base: Base, k: int) -> str:
        key = f"{base.prefix}{k}"
        self.retire([key])
        self.client.execute_command("GRAPH.COPY", base.key, key)
        return key

    def retire(self, keys) -> None:
        existing = set(self.client.execute_command("GRAPH.LIST"))
        for key in dict.fromkeys(keys):
            if key in existing:
                self.client.execute_command("GRAPH.DELETE", key)
                existing.discard(key)

    def sim_graphs(self, did: str | None = None) -> list[str]:
        prefix = f"{self.live}:sim:" + (f"{did}:" if did else "")
        return sorted(g for g in self.client.execute_command("GRAPH.LIST") if g.startswith(prefix))
