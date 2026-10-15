"""Agent sessions and the view-key algebra (master §7.2, §10.1).

A session records every binding read an agent makes. `combine` turns the reads a proposal cites into the
proposal's `read_seq` (the minimum, so the proposal is checked against the oldest state it relied on), its
`view_keys` (the union, phantom keys included) and the id-provenance set (every id those reads showed).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Read:
    """One binding read: its snapshot seq, the keys it depends on and the ids it exposed.

    For an enablement read, `tuples` are the indexed tuples and `tuple_keys[i]` the view keys of tuple i
    (the rule's view templates instantiated for it, phantom keys included); `keys` is their union.
    """

    read_id: str
    read_seq: int
    kind: str
    keys: tuple[str, ...]
    ids: frozenset[str]
    rows: list = field(default_factory=list)
    rule: str | None = None
    tuples: list = field(default_factory=list)
    tuple_keys: list = field(default_factory=list)


@dataclass(frozen=True)
class Combined:
    read_seq: int
    view_keys: tuple[str, ...]
    ids: frozenset[str]


class UnknownRead(KeyError):
    """A cited read id that this session never issued."""


def combine(reads: Iterable[Read]) -> Combined | None:
    """read_seq = min over the reads, view_keys = sorted union, ids = union; None when nothing is cited."""
    reads = list(reads)
    if not reads:
        return None
    keys: set[str] = set()
    ids: set[str] = set()
    for read in reads:
        keys.update(read.keys)
        ids.update(read.ids)
    return Combined(min(r.read_seq for r in reads), tuple(sorted(keys)), frozenset(ids))


@dataclass
class Session:
    """The agent's identity (`$agent` always comes from here) and the binding reads it can cite."""

    agent: str
    reads: dict[str, Read] = field(default_factory=dict)

    def record(self, read: Read) -> Read:
        self.reads[read.read_id] = read
        return read

    def get(self, read_id: object) -> Read:
        read = self.reads.get(read_id) if isinstance(read_id, str) else None
        if read is None:
            raise UnknownRead(read_id)
        return read

    def resolve(self, read_ids: Sequence[object]) -> list[Read]:
        return [self.get(r) for r in read_ids]
