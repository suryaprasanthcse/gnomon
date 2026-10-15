"""K2 · gapless seq (master §12.3, invariant I2): Head.seq = number of rewrites = number of distinct seqs = the
highest seq, and the lowest seq is 1. A rewrite without a seq (a ghost) breaks the count.
"""

from __future__ import annotations

from gnomon.audit import CheckResult


def check(graph) -> CheckResult:
    head = graph.ro_query("MATCH (h:Head) RETURN h.seq").result_set
    n, n_distinct, lo, hi = graph.ro_query(
        "MATCH (r:Rewrite) RETURN count(r), count(DISTINCT r.seq), min(r.seq), max(r.seq)").result_set[0]
    head_seq = head[0][0] if head else None
    if head_seq == n == n_distinct == hi and lo == 1:
        return CheckResult("K2")
    return CheckResult("K2", [{"problem": "not_gapless", "head_seq": head_seq, "n": n, "n_distinct": n_distinct,
                               "lo": lo, "hi": hi}])
