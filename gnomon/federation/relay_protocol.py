"""The relay (master §17.5): per channel A -> B, move queued messages between two tenant graphs as proposals.

    1 read   in A the queued messages of the channel, lowest chan_seq first
    2 deliver propose ReceiveMessage.<kind> in B (pid rx:<id>, read_seq 0, $m the message, $attach = JCS($m))
    3 outcome APPLIED or DUPLICATE: read B's receipt rewrite; OUT_OF_ORDER: re-deliver from B's next_seq;
              SKEW_QUARANTINED or CORRUPT (or any refusal): quarantine the channel, alert, stop
    4 ack     propose AckMessage in A (pid ack:<id>) with the receipt's pt, lc and seq
    5 advance the next queued message

The relay keeps nothing durable. Its cursor is derived from XMsg.state in A and Channel.next_seq in B, so a process that
crashes at any point is replaced by a fresh one that resumes exactly where the logs say: a repeated delivery or ack is
absorbed by pid idempotency (DUPLICATE) on both sides. Its only memory is which channels it has quarantined.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field

from gnomon.federation import messages
from gnomon.kernel.jcs import canonicalize
from gnomon.kernel.outcomes import Code, Outcome
from gnomon.kernel.service import KernelHandle

QUEUED = ("MATCH (x:XMsg) WHERE x.chan = $chan AND x.state = 'queued' RETURN x.id ORDER BY x.chan_seq LIMIT $batch")
AT_SEQ = "MATCH (x:XMsg) WHERE x.chan = $chan AND x.chan_seq = $seq RETURN x.id"
ROW = "MATCH (x:XMsg {id: $id}) RETURN properties(x)"
RECEIPT = "MATCH (r:Rewrite {id: $id}) RETURN r.seq, r.pt, r.lc, r.id"
PUBLISHABLE = ("MATCH (s:Service)<-[:CONSUMES_NOW]-(c:Consumer) "
               "WITH s, collect(DISTINCT c.tenant) AS peers "
               "WHERE coalesce(s.published_state, '') <> s.health + '/' + s.mode "
               "RETURN s.id, peers ORDER BY s.id")
CRASH_POINTS = ("before_receive", "after_receive", "before_ack", "after_ack")
MAX_RESYNCS = 100


class RelayCrash(RuntimeError):
    """Fault injection: the relay process dies at a named point. A fresh Relay resumes from the logs."""


@dataclass
class Delivery:
    """What one `deliver` pass did, as message ids in order."""

    applied: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    acked: list[str] = field(default_factory=list)
    stopped: str | None = None
    detail: dict = field(default_factory=dict)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_m(kernel: KernelHandle, mid: str) -> dict:
    """`$m` for a message of the sender's graph, from its XMsg node (only the fields of §17.3)."""
    rows = kernel.read(ROW, {"id": mid})
    if not rows:
        raise LookupError(f"no message {mid!r} in the graph")
    return messages.message(rows[0][0])


class Relay:
    def __init__(self, kernels: Mapping[str, KernelHandle], *, agent: str = "relay-1", mode: str = "live",
                 batch: int = 50) -> None:
        if mode not in ("live", "replay"):
            raise ValueError("a relay runs in live or replay mode")
        self.kernels = kernels
        self.agent = agent
        self.mode = mode
        self.batch = batch
        self.quarantined: dict[tuple[str, str], dict] = {}
        self.alerts: list[dict] = []
        self.crash_at: str | None = None

    # ── fault injection ──
    def _crash(self, point: str) -> None:
        if self.crash_at == point:
            self.crash_at = None
            raise RelayCrash(point)

    # ── proposing ──
    def _rec(self, kernel: KernelHandle, floor: int = 0) -> dict:
        """Replay mode records a time strictly after Head and after the message's clock; live mode asks the server's wall clock."""
        if self.mode == "live":
            return {}
        return {"rec_pt": max(kernel.head()["pt"], floor) + 1, "rec_lc": 0}

    def _propose(self, tenant: str, rule: str, m: Mapping, pid: str, floor: int) -> Outcome:
        kernel = self.kernels[tenant]
        attach = canonicalize(dict(m))
        return kernel.submit(rule, {"attach_sha": sha(attach)}, pid=pid, agent=self.agent, read_seq=0, attach=attach,
                             m=m, mode=self.mode, **self._rec(kernel, floor))

    def _quarantine(self, a: str, b: str, mid: str, out: Outcome, report: Delivery) -> None:
        info = {"code": out.code.value, "id": mid, "detail": dict(out.detail)}
        self.quarantined[(a, b)] = info
        self.alerts.append({"channel": f"{a}->{b}", **info})
        report.stopped, report.detail = out.code.value, info

    # ── delivery ──
    def deliver(self, a: str, b: str, *, limit: int | None = None) -> Delivery:
        """One pass over the queued messages of channel a -> b, in order. At most `limit` messages when given."""
        report = Delivery()
        if (a, b) in self.quarantined:
            report.stopped = self.quarantined[(a, b)]["code"]
            return report
        sender, chan = self.kernels[a], f"out:{b}"
        done, resyncs, pending = 0, 0, None
        while limit is None or done < limit:
            if pending is None:
                queued = sender.read(QUEUED, {"chan": chan, "batch": self.batch})
                if not queued:
                    break
                mid = queued[0][0]
            else:
                mid, pending = pending, None
            m = build_m(sender, mid)
            self._crash("before_receive")
            out = self._propose(b, f"ReceiveMessage.{m['kind']}", m, "rx:" + mid, m["s_pt"])
            if out.code is Code.OUT_OF_ORDER:
                resyncs += 1
                found = sender.read(AT_SEQ, {"chan": chan, "seq": out.detail["next_seq"]})
                if not found or resyncs > MAX_RESYNCS:
                    report.stopped, report.detail = out.code.value, dict(out.detail)
                    return report
                pending = found[0][0]
                continue
            if out.code not in (Code.APPLIED, Code.DUPLICATE):
                self._quarantine(a, b, mid, out, report)
                return report
            (report.applied if out.applied else report.duplicates).append(mid)
            self._crash("after_receive")
            if not self._ack(a, b, mid, m, report):
                return report
            done += 1
        return report

    def _ack(self, a: str, b: str, mid: str, m: Mapping, report: Delivery) -> bool:
        seq, pt, lc, rid = self.kernels[b].read(RECEIPT, {"id": "rx:" + mid})[0]
        ack = {"id": mid, "digest": m["digest"], "r_tenant": b, "r_pt": pt, "r_lc": lc, "r_seq": seq, "r_rw": rid}
        self._crash("before_ack")
        out = self._propose(a, "AckMessage", ack, "ack:" + mid, pt)
        # An ack that finds the message no longer queued has already been made by an earlier pass.
        already = out.code is Code.REJECTED_GUARD and out.detail.get("guard", "").endswith(":queued")
        if out.code not in (Code.APPLIED, Code.DUPLICATE) and not already:
            self._quarantine(a, b, mid, out, report)
            return False
        report.acked.append(mid)
        self._crash("after_ack")
        return True

    # ── publishing ──
    def publish_health(self, tenant: str) -> list[Outcome]:
        """X2 for every service that has consumers and whose state changed since it was published.

        The proposal reads at the position taken before the consumer query and cites `CONSUMES:*:svc:<id>` and the
        service's state, so a subscription that lands in between makes it stale instead of silently missing a consumer."""
        kernel = self.kernels[tenant]
        seq = kernel.head()["seq"]
        outcomes = []
        for svc, peers in kernel.read(PUBLISHABLE):
            outcomes.append(kernel.submit(
                "PublishHealth", {"svc": svc, "peers": sorted(peers)}, pid=f"pub:{svc}:{seq}", agent=self.agent,
                read_seq=seq, view_keys=(f"Service:{svc}:state", f"CONSUMES:*:svc:{svc}"), mode=self.mode,
                **self._rec(kernel)))
        return outcomes

    def sweep(self, max_rounds: int = 50) -> int:
        """Publish and deliver until nothing moves. Returns the number of messages applied to receivers."""
        total = 0
        for _ in range(max_rounds):
            moved = sum(1 for t in self.kernels for out in self.publish_health(t) if out.applied)
            for a in self.kernels:
                for b in self.kernels:
                    if a != b:
                        report = self.deliver(a, b)
                        moved += len(report.applied) + len(report.acked)
                        total += len(report.applied)
            if not moved:
                return total
        return total
