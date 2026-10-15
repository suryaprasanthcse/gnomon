"""K6 · causality (master §12.3, invariant I10): every CAUSED_BY points to a rewrite with a smaller seq, and no
Rewrite node lacks a seq (a ghost, such as one Q0's MERGE would create for an unknown cause).
"""

from __future__ import annotations

from gnomon.audit import CheckResult

FORWARD = """MATCH (r:Rewrite)-[:CAUSED_BY]->(x:Rewrite)
WHERE x.seq IS NULL OR x.seq >= r.seq
RETURN r.seq AS seq, x.id AS cause, x.seq AS cause_seq
ORDER BY seq, cause"""

GHOSTS = "MATCH (r:Rewrite) WHERE r.seq IS NULL RETURN r.id AS id ORDER BY id"


def check(graph) -> CheckResult:
    findings = [{"problem": "causal_order", "seq": seq, "cause": cause, "cause_seq": cause_seq}
                for seq, cause, cause_seq in graph.ro_query(FORWARD).result_set]
    findings += [{"problem": "ghost_rewrite", "id": rid} for (rid,) in graph.ro_query(GHOSTS).result_set]
    return CheckResult("K6", findings)
