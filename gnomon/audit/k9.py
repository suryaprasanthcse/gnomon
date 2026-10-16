"""K9 · federation audit (master §17.8, invariant I14): channel gaplessness, exactly-once delivery, causality, digests
and sys anchors, across the tenant graphs of a federation and its registry graph `t:_sys`.

    K9a each sender     out-channel XMsg.chan_seq values are gapless from 1 and Channel.next_seq = max + 1
    K9b each receiver   in-channel chan_seq values are gapless from 1 and next_seq - 1 = the number received
    K9c both ends       every message received has a sent counterpart (same id and digest) and no id is received twice;
                        every delivered message has a received counterpart
    K9d both ends       (r_pt, r_lc) > (s_pt, s_lc) for every received or delivered message, and the receiver's
                        Rewrite rx:<id> has exactly the recorded pt, lc and seq
    K9e every graph     Kernel.digest(fields) = digest for every XMsg, sent or received
    K9f registry        each tenant's Head.sys_anchor names the sys RegisterTenant rewrite of that tenant, with the
                        same hash, and each channel's sys_peering is an active Peering

A check names the findings that make it fail (DECISIONS D-28). Graphs are keyed by tenant id.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from gnomon.audit import CheckResult
from gnomon.federation import messages, sys as fsys

NAMES = ("K9a", "K9b", "K9c", "K9d", "K9e", "K9f")
CHANNELS = "MATCH (c:Channel) RETURN c.key, c.dir, c.peer, c.next_seq, c.sys_peering ORDER BY c.key"
MESSAGES = "MATCH (x:XMsg) RETURN properties(x) ORDER BY x.chan, x.chan_seq, x.id"
RECEIPTS = "MATCH (r:Rewrite) WHERE r.id STARTS WITH 'rx:' RETURN r.id, r.seq, r.pt, r.lc"
HEAD = "MATCH (h:Head) RETURN h.tenant, h.sys_anchor"
SYS_REWRITE = "MATCH (r:Rewrite {seq: $seq}) RETURN r.id, r.hash, r.rule, r.params"
SYS_TENANT = "MATCH (t:Tenant {id: $id}) RETURN t.state, t.registered_seq"
SYS_PEERING = "MATCH (p:Peering {key: $key}) RETURN p.state"
DIGESTS = "UNWIND $items AS it RETURN it.id, Kernel.digest(it.fields)"


class _World:
    """Everything K9 reads, once per audit: channels and messages of each graph, and each graph's receipts."""

    def __init__(self, graphs: Mapping[str, object], sys_graph) -> None:
        self.graphs, self.sys_graph = graphs, sys_graph
        self.channels = {t: {row[0]: dict(zip(("key", "dir", "peer", "next_seq", "sys_peering"), row))
                             for row in g.ro_query(CHANNELS).result_set} for t, g in graphs.items()}
        self.messages = {t: [row[0] for row in g.ro_query(MESSAGES).result_set] for t, g in graphs.items()}
        self.receipts = {t: {rid: (seq, pt, lc) for rid, seq, pt, lc in g.ro_query(RECEIPTS).result_set}
                         for t, g in graphs.items()}

    def sent(self, tenant: str) -> list[dict]:
        return [x for x in self.messages[tenant] if str(x.get("chan", "")).startswith("out:")]

    def received(self, tenant: str) -> list[dict]:
        return [x for x in self.messages[tenant] if str(x.get("chan", "")).startswith("in:")]


def _gapless(seqs: list) -> bool:
    return sorted(seqs) == list(range(1, len(seqs) + 1))


def k9a(w: _World) -> CheckResult:
    findings = []
    for tenant in sorted(w.graphs):
        sent: dict[str, list] = {}
        for x in w.sent(tenant):
            sent.setdefault(x["chan"], []).append(x["chan_seq"])
        for key in sorted(set(sent) | {k for k, c in w.channels[tenant].items() if c["dir"] == "out"}):
            channel, seqs = w.channels[tenant].get(key), sent.get(key, [])
            if channel is None:
                findings.append({"graph": tenant, "chan": key, "problem": "no_channel"})
            elif not _gapless(seqs):
                findings.append({"graph": tenant, "chan": key, "problem": "gap", "chan_seqs": sorted(seqs)})
            elif channel["next_seq"] != len(seqs) + 1:
                findings.append({"graph": tenant, "chan": key, "problem": "next_seq", "next_seq": channel["next_seq"],
                                 "expected": len(seqs) + 1})
    return CheckResult("K9a", findings)


def k9b(w: _World) -> CheckResult:
    findings = []
    for tenant in sorted(w.graphs):
        got: dict[str, list] = {}
        for x in w.received(tenant):
            got.setdefault(x["chan"], []).append(x["chan_seq"])
        for key in sorted(set(got) | {k for k, c in w.channels[tenant].items() if c["dir"] == "in"}):
            channel, seqs = w.channels[tenant].get(key), got.get(key, [])
            if channel is None:
                findings.append({"graph": tenant, "chan": key, "problem": "no_channel"})
            elif not _gapless(seqs):
                findings.append({"graph": tenant, "chan": key, "problem": "gap", "chan_seqs": sorted(seqs)})
            elif channel["next_seq"] - 1 != len(seqs):
                findings.append({"graph": tenant, "chan": key, "problem": "count", "next_seq": channel["next_seq"],
                                 "received": len(seqs)})
    return CheckResult("K9b", findings)


def _pairs(w: _World):
    """(sender, receiver, sent messages by id, received messages in order) for every ordered pair of audited tenants."""
    for a in sorted(w.graphs):
        for b in sorted(w.graphs):
            if a != b:
                sent = {x["id"]: x for x in w.sent(a) if x["chan"] == f"out:{b}"}
                got = [x for x in w.received(b) if x["chan"] == f"in:{a}"]
                yield a, b, sent, got


def k9c(w: _World) -> CheckResult:
    findings = []
    for a, b, sent, got in _pairs(w):
        seen: set[str] = set()
        for y in got:
            x = sent.get(y["id"])
            if y["id"] in seen:
                findings.append({"from": a, "to": b, "id": y["id"], "problem": "received_twice"})
            elif x is None:
                findings.append({"from": a, "to": b, "id": y["id"], "problem": "never_sent"})
            elif x.get("digest") != y.get("digest"):
                findings.append({"from": a, "to": b, "id": y["id"], "problem": "digest_differs"})
            seen.add(y["id"])
        for mid, x in sent.items():
            if x.get("state") == "delivered" and mid not in seen:
                findings.append({"from": a, "to": b, "id": mid, "problem": "delivered_never_received"})
    return CheckResult("K9c", findings)


def k9d(w: _World) -> CheckResult:
    findings = []
    for a, b, sent, got in _pairs(w):
        receipts = w.receipts[b]
        for y in got:
            x = sent.get(y["id"])
            receipt = receipts.get("rx:" + y["id"])
            if x is not None and (y["r_pt"], y["r_lc"]) <= (x["s_pt"], x["s_lc"]):
                findings.append({"from": a, "to": b, "id": y["id"], "problem": "receipt_not_after_send"})
            if receipt is None or receipt != (y["r_seq"], y["r_pt"], y["r_lc"]):
                findings.append({"from": a, "to": b, "id": y["id"], "problem": "receipt_differs", "xmsg":
                                 [y["r_seq"], y["r_pt"], y["r_lc"]], "rewrite": list(receipt) if receipt else None})
        for mid, x in sent.items():
            if x.get("state") != "delivered":
                continue
            receipt = receipts.get("rx:" + mid)
            if (x["r_pt"], x["r_lc"]) <= (x["s_pt"], x["s_lc"]):
                findings.append({"from": a, "to": b, "id": mid, "problem": "ack_not_after_send"})
            if receipt is None or receipt != (x["r_seq"], x["r_pt"], x["r_lc"]):
                findings.append({"from": a, "to": b, "id": mid, "problem": "ack_differs", "acked":
                                 [x["r_seq"], x["r_pt"], x["r_lc"]], "rewrite": list(receipt) if receipt else None})
    return CheckResult("K9d", findings)


def k9e(w: _World) -> CheckResult:
    findings = []
    for tenant in sorted(w.graphs):
        items, own = [], {}
        for x in w.messages[tenant]:
            try:
                items.append({"id": x["id"], "fields": messages.digest_fields(x)})
            except messages.UnknownKind:
                findings.append({"graph": tenant, "id": x.get("id"), "problem": "unknown_kind"})
            own[x["id"]] = x.get("digest")
        if items:
            for mid, again in w.graphs[tenant].ro_query(DIGESTS, {"items": items}).result_set:
                if again != own[mid]:
                    findings.append({"graph": tenant, "id": mid, "problem": "digest_differs"})
    return CheckResult("K9e", findings)


def k9f(w: _World) -> CheckResult:
    findings = []
    for tenant in sorted(w.graphs):
        head = w.graphs[tenant].ro_query(HEAD).result_set
        anchor = fsys.parse_anchor(head[0][1]) if head else None
        if anchor is None:
            findings.append({"graph": tenant, "problem": "no_anchor"})
        else:
            rows = w.sys_graph.ro_query(SYS_REWRITE, {"seq": anchor["sys_seq"]}).result_set
            if not rows:
                findings.append({"graph": tenant, "problem": "anchor_not_in_sys_log", "anchor": anchor})
            else:
                rid, digest, rule, params = rows[0]
                registered = _registered(params)
                if (rid, digest, rule) != (anchor["sys_rw"], anchor["sys_hash"], "RegisterTenant"):
                    findings.append({"graph": tenant, "problem": "anchor_differs", "anchor": anchor,
                                     "sys": {"id": rid, "hash": digest, "rule": rule}})
                elif registered != tenant:
                    findings.append({"graph": tenant, "problem": "anchor_names_another_tenant", "tenant": registered})
        for key, channel in sorted(w.channels[tenant].items()):
            peering = channel.get("sys_peering")
            if peering is not None:
                rows = w.sys_graph.ro_query(SYS_PEERING, {"key": peering}).result_set
                if not rows or rows[0][0] != "active":
                    findings.append({"graph": tenant, "chan": key, "problem": "peering_not_active", "peering": peering})
    return CheckResult("K9f", findings)


def _registered(params: str | None) -> str | None:
    try:
        return json.loads(params or "{}").get("id")
    except (ValueError, AttributeError):
        return None


CHECKS = {"K9a": k9a, "K9b": k9b, "K9c": k9c, "K9d": k9d, "K9e": k9e, "K9f": k9f}


def check(graphs: Mapping[str, object], sys_graph, names=NAMES) -> list[CheckResult]:
    """Runs the named K9 checks over the tenant graphs (keyed by tenant id) and the registry graph."""
    world = _World(graphs, sys_graph)
    return [CHECKS[name](world) for name in NAMES if name in names]
