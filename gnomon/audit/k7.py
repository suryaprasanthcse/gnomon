"""K7 skeleton · decisions are what they say (master §15.13 steps 1 and 8, invariant I11).

Step 1: for every `(Decision)-[:COMMITTED_AS]->(Rewrite)`, the envelope in `attach` hashes to its own sha and to the
`decision_sha` in the rewrite's params, and the Decision, Candidate and Evidence nodes equal the parts of the envelope.
Step 8 (I11): every rewrite of an action-class rule has exactly one Decision and every Decision exactly one
`COMMITTED_AS`. Re-simulation (steps 2-7) belongs to the Executive and arrives with A11.
"""

from __future__ import annotations

import hashlib
import json

from gnomon.audit import CheckResult
from gnomon.kernel.jcs import canonicalize

DECISIONS = """MATCH (x:Decision)-[:COMMITTED_AS]->(r:Rewrite)
RETURN x.id AS id, properties(x) AS decision, r.id AS rewrite, r.attach AS attach, r.params AS params,
       r.read_seq AS read_seq
ORDER BY id, rewrite"""
CANDIDATES = """MATCH (:Decision {id: $id})-[:CONSIDERED]->(c:Candidate)
RETURN c.cid AS cid, properties(c) AS props ORDER BY cid"""
EVIDENCE = """MATCH (:Decision {id: $id})-[:JUSTIFIED_BY]->(e:Evidence)
OPTIONAL MATCH (e)-[c:CITES]->(s:Entity)
RETURN e.eid AS eid, properties(e) AS props, collect([c.fid, s.uid]) AS cites ORDER BY eid"""
ACTION_SHAS = "MATCH (ru:Rule) WHERE ru.class = 'action' RETURN ru.sha AS sha ORDER BY sha"
ACTION_REWRITES = """MATCH (r:Rewrite) WHERE r.rule_sha IN $shas
OPTIONAL MATCH (x:Decision)-[c:COMMITTED_AS]->(r)
WITH r, count(c) AS n WHERE n <> 1
RETURN r.id AS rewrite, n ORDER BY rewrite"""
ORPHANS = """MATCH (x:Decision)
OPTIONAL MATCH (x)-[c:COMMITTED_AS]->(:Rewrite)
WITH x, count(c) AS n WHERE n <> 1
RETURN x.id AS id, n ORDER BY id"""

DECISION_FIELDS = (("id", "did"), ("attempt", "attempt"), ("sha", "sha"), ("kind", "kind"), ("proposer", "proposer"),
                   ("rationale_ref", "rationale_ref"), ("scorer", "scorer"), ("scorer_sha", "scorer_sha"),
                   ("hops", "hops"), ("lambda_n", "lambda_n"), ("lambda_d", "lambda_d"), ("tau", "tau"),
                   ("margin", "margin"), ("weight_scale", "weight_scale"), ("footprint_digest", "footprint_digest"),
                   ("workload_digest", "workload_digest"))
CANDIDATE_FIELDS = ("k", "rule", "params_sha", "outcome", "reason", "benefit", "harm", "teams_new", "score_num",
                    "plan_before", "plan_after", "measure_digest", "won")


def _differs(props: dict, expected: dict, fields) -> list[str]:
    return [name for name, key in fields if props.get(name) != expected.get(key)]


def check(graph) -> CheckResult:
    findings: list[dict] = []
    for did, node, rewrite, attach, params, read_seq in graph.ro_query(DECISIONS).result_set:
        try:
            d = json.loads(attach)
            decision_sha = json.loads(params).get("decision_sha")
        except (TypeError, ValueError):
            findings.append({"problem": "attach_unparseable", "id": did, "rewrite": rewrite})
            continue
        body = {k: v for k, v in d.items() if k != "sha"}
        recomputed = hashlib.sha256(canonicalize(body).encode("utf-8")).hexdigest()
        if not (recomputed == d.get("sha") == decision_sha):
            findings.append({"problem": "sha_mismatch", "id": did, "rewrite": rewrite, "recomputed": recomputed,
                             "envelope": d.get("sha"), "params": decision_sha})
        bad = _differs(node, d, DECISION_FIELDS)
        if not (node.get("read_seq") == d.get("read_seq") == read_seq):
            bad.append("read_seq")  # the base snapshot named by the envelope, the node and the rewrite agree
        if bad:
            findings.append({"problem": "node_mismatch", "node": "Decision", "id": did, "props": bad})
        candidates = {cid: props for cid, props in graph.ro_query(CANDIDATES, {"id": did}).result_set}
        expected = {f"{d['did']}:{c['k']}": c for c in d.get("candidates", [])}
        for cid in sorted(set(candidates) | set(expected)):
            props, want = candidates.get(cid), expected.get(cid)
            if props is None or want is None:
                findings.append({"problem": "node_mismatch", "node": "Candidate", "id": cid, "props": ["presence"]})
                continue
            bad = [f for f in CANDIDATE_FIELDS if props.get(f) != want.get(f)]
            if bad:
                findings.append({"problem": "node_mismatch", "node": "Candidate", "id": cid, "props": bad})
        evidence = {eid: (props, sorted(map(tuple, cites))) for eid, props, cites in
                    graph.ro_query(EVIDENCE, {"id": did}).result_set}
        wanted = {f"{d['did']}:{e['ord']}": e for e in d.get("evidence", [])}
        for eid in sorted(set(evidence) | set(wanted)):
            if eid not in evidence or eid not in wanted:
                findings.append({"problem": "node_mismatch", "node": "Evidence", "id": eid, "props": ["presence"]})
                continue
            props, cites = evidence[eid]
            want = wanted[eid]
            bad = [f for f in ("ord", "algo", "role", "path") if props.get(f) != want.get(f)]
            if cites != sorted((c["fid"], c["s"]) for c in want.get("cites", [])):
                bad.append("cites")
            if bad:
                findings.append({"problem": "node_mismatch", "node": "Evidence", "id": eid, "props": bad})
    shas = [sha for (sha,) in graph.ro_query(ACTION_SHAS).result_set]
    if shas:
        for rewrite, n in graph.ro_query(ACTION_REWRITES, {"shas": shas}).result_set:
            findings.append({"problem": "i11_no_decision" if n == 0 else "i11_many_decisions", "rewrite": rewrite,
                             "decisions": n})
    findings += [{"problem": "i11_orphan_decision", "id": did, "commits": n}
                 for did, n in graph.ro_query(ORPHANS).result_set]
    return CheckResult("K7", findings)
