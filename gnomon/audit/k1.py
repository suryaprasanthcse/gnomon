"""K1 · hash chain (master §12.3, invariant I3): every rewrite's hash recomputes from its fields, each `prev`
is its predecessor's hash, and Head.hash is the last hash. Recomputation runs in batches of seq; the
linkage is checked client side, across batch borders.
"""

from __future__ import annotations

import redis

from gnomon.audit import CheckResult
from gnomon.kernel.service import ZERO64

QUERY = """MATCH (r:Rewrite)
WHERE r.seq > $from AND r.seq <= $to
RETURN r.seq AS seq, r.prev AS prev, r.hash AS hash,
       Kernel.chain(r.prev, r.id, r.seq, r.pt, r.lc, r.rule_sha, r.agent, r.params,
                    r.read_seq, Kernel.digest(r.view_keys)) AS recomputed
ORDER BY seq"""


def check(graph, batch: int = 500) -> CheckResult:
    findings: list[dict] = []
    head = graph.ro_query("MATCH (h:Head) RETURN h.hash").result_set
    top = graph.ro_query("MATCH (r:Rewrite) RETURN max(r.seq)").result_set[0][0] or 0
    expected_seq, last = 1, ZERO64
    for lo in range(0, top, batch):
        try:
            rows = graph.ro_query(QUERY, {"from": lo, "to": lo + batch}).result_set
        except redis.ResponseError as exc:
            findings.append({"seq": lo + 1, "problem": "unhashable", "detail": str(exc)})
            continue
        for seq, prev, digest, recomputed in rows:
            if seq != expected_seq:
                findings.append({"seq": expected_seq, "problem": "gap", "next": seq})
            if prev != last:
                findings.append({"seq": seq, "problem": "prev_mismatch"})
            if recomputed != digest:
                findings.append({"seq": seq, "problem": "hash_mismatch"})
            expected_seq, last = seq + 1, digest
    if not head or head[0][0] != last:
        findings.append({"seq": top, "problem": "head_hash"})
    return CheckResult("K1", findings)
