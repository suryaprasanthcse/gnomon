"""K5 · domain invariants (master §12.3): a leases (I6) · b no overlapping current versions (I7) · c citations
live at the citing rewrite (I8) · d projections exact (I9). K5b and K5d are generated per FactType.
"""

from __future__ import annotations

import re

from gnomon.audit import CheckResult
from gnomon.kernel.service import INF

REL = re.compile(r"^[A-Z][A-Z0-9_]*$")

LEASES = """MATCH (t:Task)<-[l:HOLDS]-(:Agent)
WHERE l.closed_seq IS NULL
WITH t, count(l) AS n
WHERE n > 1
RETURN t.id AS task, n
ORDER BY task"""

OVERLAP_FUNCTIONAL = """MATCH (s:Entity)-[a:{rel}]->(), (s)-[b:{rel}]->()
WHERE a.te = $INF AND b.te = $INF AND a.fid < b.fid AND a.vf < b.vt AND b.vf < a.vt
RETURN s.uid AS subject, a.fid AS a, b.fid AS b
ORDER BY subject, a, b"""

OVERLAP_SET = """MATCH (s:Entity)-[a:{rel}]->(o), (s)-[b:{rel}]->(o)
WHERE a.te = $INF AND b.te = $INF AND a.fid < b.fid AND a.vf < b.vt AND b.vf < a.vt
RETURN s.uid AS subject, a.fid AS a, b.fid AS b
ORDER BY subject, a, b"""

# Counted with sum(CASE …) rather than a WHERE on the OPTIONAL MATCH (FACTS S25, DECISIONS D-25).
CITATIONS = """MATCH (m)-[c:CITES]->(s:Entity)
OPTIONAL MATCH (r:Rewrite {id: m.rw})
OPTIONAL MATCH (s)-[f]-()
WITH m, c, r, sum(CASE WHEN f.fid = c.fid AND f.ts < r.seq AND r.seq <= f.te AND f.vf <= r.pt AND r.pt < f.vt
                       THEN 1 ELSE 0 END) AS ok
WHERE ok = 0
RETURN labels(m) AS citer, coalesce(m.uid, m.eid) AS citing, c.fid AS fid, r.seq AS at_seq
ORDER BY citing, fid"""


def _fact_types(graph) -> list[tuple[str, str, str | None]]:
    return graph.ro_query("MATCH (ft:FactType) RETURN ft.rel, ft.cardinality, ft.projection ORDER BY ft.rel").result_set


def _bad_name(name: str) -> dict | None:
    return None if isinstance(name, str) and REL.match(name) else {"problem": "bad_type_name", "name": name}


def leases(graph) -> CheckResult:
    return CheckResult("K5a", [{"task": task, "open_leases": n} for task, n in graph.ro_query(LEASES).result_set])


def overlaps(graph) -> CheckResult:
    findings = []
    for rel, cardinality, _ in _fact_types(graph):
        bad = _bad_name(rel)
        if bad:
            findings.append(bad)
            continue
        query = (OVERLAP_FUNCTIONAL if cardinality == "functional" else OVERLAP_SET).replace("{rel}", rel)
        findings += [{"rel": rel, "subject": s, "a": a, "b": b}
                     for s, a, b in graph.ro_query(query, {"INF": INF}).result_set]
    return CheckResult("K5b", findings)


def citations(graph) -> CheckResult:
    return CheckResult("K5c", [{"citer": citer, "citing": citing, "fid": fid, "at_seq": at_seq}
                               for citer, citing, fid, at_seq in graph.ro_query(CITATIONS).result_set])


def projections(graph) -> CheckResult:
    """Current open-ended facts and projection edges are compared as sets of (fid, start, end)."""
    findings = []
    for rel, _, proj in _fact_types(graph):
        if proj is None:
            continue
        bad = _bad_name(rel) or _bad_name(proj)
        if bad:
            findings.append(bad)
            continue
        facts = {tuple(row) for row in graph.ro_query(
            f"MATCH (a)-[f:{rel}]->(b) WHERE f.te = $INF AND f.vt = $INF RETURN f.fid, ID(a), ID(b)",
            {"INF": INF}).result_set}
        projected = {tuple(row) for row in graph.ro_query(
            f"MATCH (a)-[p:{proj}]->(b) RETURN p.fid, ID(a), ID(b)").result_set}
        findings += [{"problem": "missing_projection", "rel": rel, "fid": fid} for fid, _, _ in sorted(facts - projected)]
        findings += [{"problem": "orphan_projection", "proj": proj, "fid": fid}
                     for fid, _, _ in sorted(projected - facts, key=repr)]
    return CheckResult("K5d", findings)


def check(graph) -> list[CheckResult]:
    return [leases(graph), overlaps(graph), citations(graph), projections(graph)]
