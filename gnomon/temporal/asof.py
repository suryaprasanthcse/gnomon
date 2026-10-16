"""As-of projection graphs (master §19.1): scratch copies whose DEP_NOW and OWNER_NOW edges describe a past time.

FalkorDB algorithms filter by relationship type, so they always see current topology. To run an unmodified algorithm on
the topology of valid time `T` (current belief) or of the belief after rewrite `S`, the live graph is copied, its two
current projections are dropped, and they are rebuilt from the bitemporal facts. Node properties such as health and mode
are current-only and are not time-travelled; state as of a seq belongs to a replay graph (§12.1).

Scratch graphs are named `<live>:asof:<T>` and `<live>:asof:seq<S>`. Each is recorded, with its creation time in
milliseconds, in the Redis hash `<live>:asofstate`; the sweeper deletes the ones older than ASOF_TTL_MS. A scratch graph
is usable once it holds its `AsofMeta` node, which is written last, so a half-built copy is never reused.
"""

from __future__ import annotations

import time
from collections.abc import Callable

INF = 9007199254740991
ASOF_TTL_MS = 600_000
TAG = ":asof:"
STATE = ":asofstate"

DROP = ("MATCH ()-[p:DEP_NOW]->() DELETE p", "MATCH ()-[p:OWNER_NOW]->() DELETE p")
# Valid time T, current belief (§19.1 step 3).
AT_TIME = "f.te = $INF AND f.vf <= $T AND $T < f.vt"
# The belief after rewrite S (§19.1 closing paragraph).
AT_SEQ = "f.ts <= $S AND $S < f.te AND f.vt = $INF"
REBUILD = (
    "MATCH (a:Service)-[f:DEPENDS_ON]->(b) WHERE {when} CREATE (a)-[:DEP_NOW {{fid: f.fid, hard: f.hard}}]->(b)",
    "MATCH (s:Service)-[f:OWNED_BY]->(t:Team) WHERE {when} CREATE (s)-[:OWNER_NOW {{fid: f.fid}}]->(t)",
)
READY = "MATCH (m:AsofMeta) RETURN count(m)"


class AsofError(ValueError):
    """An as-of request that names no time, both a time and a seq, or a value that is not a non-negative integer."""


def _nat(name: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < INF:
        raise AsofError(f"{name} must be a non-negative integer, got {value!r}")
    return value


def name_of(live: str, at: int | None = None, seq: int | None = None) -> str:
    """`<live>:asof:<T>` for valid time T, or `<live>:asof:seq<S>` for the belief after rewrite S."""
    if (at is None) == (seq is None):
        raise AsofError("give exactly one of asof (a valid time) and asof_seq (a rewrite seq)")
    return f"{live}{TAG}{_nat('asof', at)}" if seq is None else f"{live}{TAG}seq{_nat('asof_seq', seq)}"


def _wall() -> int:
    return time.time_ns() // 1_000_000


def _scratch(client, live: str) -> list[str]:
    return sorted(g for g in client.execute_command("GRAPH.LIST") if g.startswith(live + TAG))


def build(db, client, live: str, *, at: int | None = None, seq: int | None = None,
          now: Callable[[], int] | None = None) -> str:
    """The scratch graph for `at` or `seq`: reused when it is complete, else copied and projected. Returns its name.
    Reuse renews the creation time, so a graph that is in use is not swept from under its reader."""
    key = name_of(live, at, seq)
    stamp = (now or _wall)()
    exists = key in set(client.execute_command("GRAPH.LIST"))
    graph = db.select_graph(key)
    if exists and graph.ro_query(READY).result_set[0][0] > 0:
        client.hset(live + STATE, key, stamp)
        return key
    if exists:  # a build that never finished
        client.execute_command("GRAPH.DELETE", key)
    client.hset(live + STATE, key, stamp)  # recorded first: a crash leaves a graph the sweeper knows
    client.execute_command("GRAPH.COPY", live, key)
    params = {"INF": INF, "T": at} if seq is None else {"INF": INF, "S": seq}
    for text in DROP:
        graph.query(text)
    for text in REBUILD:
        graph.query(text.format(when=AT_TIME if seq is None else AT_SEQ), params)
    graph.query("CREATE (:AsofMeta {at: $at, seq: $seq, built: $built})", {"at": at, "seq": seq, "built": stamp})
    return key


def sweep(client, live: str, now: int, ttl_ms: int = ASOF_TTL_MS) -> list[str]:
    """Delete the scratch graphs of `live` created ttl_ms or more before `now`, and any that were never recorded.
    Returns the names deleted."""
    recorded = {k: int(v) for k, v in client.hgetall(live + STATE).items()}
    gone = []
    for key in _scratch(client, live):
        if key not in recorded or now - recorded[key] >= ttl_ms:
            client.execute_command("GRAPH.DELETE", key)
            gone.append(key)
    for key in list(recorded):
        if key in gone or key not in set(client.execute_command("GRAPH.LIST")):
            client.hdel(live + STATE, key)
    return gone


class AsofGraphs:
    """What the MCP server holds: builds (or reuses) the scratch graph for a request and returns it as a graph."""

    def __init__(self, db, client, live: str, clock: Callable[[], int] | None = None) -> None:
        self.db, self.client, self.live, self.clock = db, client, live, clock

    def __call__(self, at: int | None = None, seq: int | None = None):
        return self.db.select_graph(build(self.db, self.client, self.live, at=at, seq=seq, now=self.clock))
