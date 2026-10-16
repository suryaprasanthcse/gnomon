"""The federated log and cross-tenant explanation (master §17.7).

`merge` reads `Rewrite (pt, lc, seq, id, rule, agent)` from every tenant graph and orders the union by
`(pt, lc, tenant, seq)`. By F4 that is a linearization consistent with happens-before: a local step strictly increases the
hybrid clock, and so does a receipt over the send it answers (F3).

`why` follows a rewrite back through its causes. When the chain reaches the receipt of a message, it follows the XMsg's
`origin_tenant` and `origin_rw` to the sending rewrite in the other graph and carries on with that graph's CAUSED_BY
chain. Graph edges cannot cross graphs, so this is a client-side join.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

REWRITES = "MATCH (r:Rewrite) RETURN r.pt, r.lc, r.seq, r.id, r.rule, r.agent"
LAST_WRITER = "MATCH (e:Entity {uid: $uid}) RETURN e.rw"
REWRITE = "MATCH (r:Rewrite {id: $id}) RETURN r.seq, r.rule, r.agent"
CAUSES = "MATCH (r:Rewrite {id: $id})-[:CAUSED_BY]->(c:Rewrite) RETURN c.id ORDER BY c.seq DESC"
ORIGIN = "MATCH (x:XMsg {id: $id}) RETURN x.origin_tenant, x.origin_rw"
RECEIPT_RULES = "ReceiveMessage."
RECEIPT_PID = "rx:"
MAX_HOPS = 64


@dataclass(frozen=True)
class LogEntry:
    pt: int
    lc: int
    tenant: str
    seq: int
    id: str
    rule: str
    agent: str

    def as_dict(self) -> dict:
        return {"pt": self.pt, "lc": self.lc, "tenant": self.tenant, "seq": self.seq, "id": self.id,
                "rule": self.rule, "agent": self.agent}


def merge(graphs: Mapping[str, object], *, from_pt: int | None = None, to_pt: int | None = None) -> list[LogEntry]:
    """Every tenant's rewrites in one list ordered by (pt, lc, tenant, seq); `from_pt`/`to_pt` bound pt inclusively."""
    entries = []
    for tenant, graph in graphs.items():
        for pt, lc, seq, rid, rule, agent in graph.ro_query(REWRITES).result_set:
            if (from_pt is None or pt >= from_pt) and (to_pt is None or pt <= to_pt):
                entries.append(LogEntry(pt, lc, tenant, seq, rid, rule, agent))
    return sorted(entries, key=lambda e: (e.pt, e.lc, e.tenant, e.seq))


def _hop(graphs: Mapping[str, object], tenant: str, rid: str) -> dict | None:
    rows = graphs[tenant].ro_query(REWRITE, {"id": rid}).result_set
    if not rows:
        return None
    seq, rule, agent = rows[0]
    return {"tenant": tenant, "id": rid, "seq": seq, "rule": rule, "agent": agent}


def why(graphs: Mapping[str, object], tenant: str, uid: str) -> dict | None:
    """The rewrites behind an entity of `tenant`, newest first, across every graph the chain reaches."""
    written = graphs[tenant].ro_query(LAST_WRITER, {"uid": uid}).result_set
    if not written:
        return None
    hops: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def visit(at: str, rid: str) -> None:
        if (at, rid) in seen or len(hops) >= MAX_HOPS:
            return
        seen.add((at, rid))
        hop = _hop(graphs, at, rid)
        if hop is None:
            return
        hops.append(hop)
        if hop["rule"].startswith(RECEIPT_RULES) and rid.startswith(RECEIPT_PID):
            origin = graphs[at].ro_query(ORIGIN, {"id": rid[len(RECEIPT_PID):]}).result_set
            if origin and origin[0][0] in graphs:
                visit(origin[0][0], origin[0][1])
        for (cause,) in graphs[at].ro_query(CAUSES, {"id": rid}).result_set:
            visit(at, cause)

    visit(tenant, written[0][0])
    return {"entity": uid, "tenant": tenant, "hops": hops}
