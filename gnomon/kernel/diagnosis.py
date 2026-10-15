"""Diagnosis of a zero-row apply (master §6.10): read-only probes, run in order; the first that fails names it.

D1 RULE_PINNED · D2 CALLER_DENIED · D3 DUPLICATE / PID_CONFLICT · D4 INVALID_VIEW · D5 REJECTED_STALE ·
D6 REJECTED_DECISION (decision rules: the P1 checks, one sub-reason each) ·
D8 REJECTED_GUARD (the rule's guard probes, in order) · D9 INDETERMINATE (everything passes now).
Genesis (prelude PG) has no rule, agent or log yet, so it goes straight to its guard probes.
D6 and D7 belong to decision and federation rules (A10, A13).
"""

from __future__ import annotations

import hashlib

from gnomon.kernel.outcomes import Code, Outcome
from gnomon.kernel.registry import RuleDef

D1 = "MATCH (ru:Rule {name: $rule, sha: $rule_sha}) WHERE ru.active = true RETURN count(ru) AS pinned"
D2 = (
    "MATCH (ru:Rule {sha: $rule_sha}) OPTIONAL MATCH (a:Agent {id: $agent}) "
    "RETURN a IS NOT NULL AND a.active = true AND a.role IN ru.caller AS allowed"
)
D3 = "MATCH (r:Rewrite {id: $pid}) RETURN r.seq AS seq, r.hash AS hash, r.rule_sha AS rule_sha, r.params AS params"
D4 = "MATCH (h:Head) RETURN h.seq AS head_seq"
D5 = (
    "MATCH (kv:KeyVersion) WHERE kv.key IN $view_keys AND kv.seq > $read_seq "
    "OPTIONAL MATCH (w:Rewrite {id: kv.rw}) "
    "RETURN kv.key AS key, kv.seq AS changed_at, kv.rw AS by_rewrite, w.agent AS by_agent, w.rule AS by_rule "
    "ORDER BY changed_at, key"
)
D6_SCORER = "MATCH (s:Scorer {name: $name, sha: $sha}) RETURN s.active AS active"
D6_APPROVAL = (
    "MATCH (a:Approval {aid: $aid}) RETURN a.state AS state, a.verdict AS verdict, a.decision_sha AS decision_sha"
)
# The P1 cite predicate at the rewrite's own time (the tick: wall in live mode, the recorded pt otherwise).
D6_CITE = (
    "MATCH (h:Head) WITH h, CASE WHEN $mode = 'live' THEN (CASE WHEN timestamp() > h.pt THEN timestamp() ELSE h.pt END) "
    "ELSE $rec_pt END AS now "
    "MATCH (cs:Entity {uid: $s})-[cf]-() "
    "WHERE cf.fid = $fid AND cf.te = $INF AND cf.vf <= now AND now < cf.vt RETURN count(DISTINCT cf.fid) AS live"
)
STALE_FIELDS = ("key", "changed_at", "by_rewrite", "by_agent", "by_rule")


def decision_reason(graph, rule: RuleDef, params: dict) -> str | None:
    """D6: the first P1 check that fails, as a sub-reason, in P1 order; None when every check holds."""
    d, p = params["d"], params["p"]

    def rows(cypher: str, **extra) -> list:
        return graph.ro_query(cypher, {**params, **extra}).result_set

    scorer = rows(D6_SCORER, name=d["scorer"], sha=d["scorer_sha"])
    if not scorer or scorer[0][0] is not True:
        return "scorer_inactive"
    if d["read_seq"] != params["read_seq"]:
        return "read_seq_mismatch"
    if hashlib.sha256(params["attach"].encode("utf-8")).hexdigest() != p.get("attach_sha"):
        return "attach_sha_mismatch"
    if p.get("decision_sha") != d["sha"]:
        return "decision_sha_mismatch"
    if rule.gated:
        approval = rows(D6_APPROVAL, aid=d["aid"])
        if not approval:
            return "approval_missing"
        state, verdict, decision_sha = approval[0]
        if state != "decided":
            return "approval_pending"
        if verdict != "approve":
            return "approval_vetoed"
        if decision_sha != d["sha"]:
            return "approval_sha_mismatch"
    for cite in d["cites"]:
        live = rows(D6_CITE, s=cite["s"], fid=cite["fid"])
        if not live or live[0][0] == 0:
            return "cite_not_live"
    return None


def diagnose(graph, rule: RuleDef, params: dict) -> Outcome:
    def rows(cypher: str) -> list:
        return graph.ro_query(cypher, params).result_set

    if rule.prelude != "PG":
        if rows(D1)[0][0] == 0:
            return Outcome(Code.RULE_PINNED, detail={"rule": params["rule"], "rule_sha": params["rule_sha"]})
        allowed = rows(D2)
        if not allowed or allowed[0][0] is not True:
            return Outcome(Code.CALLER_DENIED, detail={"agent": params["agent"], "rule": params["rule"]})
        existing = rows(D3)
        if existing:
            seq, digest, rule_sha, stored_params = existing[0]
            if rule_sha == params["rule_sha"] and stored_params == params["params_json"]:
                return Outcome(Code.DUPLICATE, seq=seq, hash=digest)
            return Outcome(Code.PID_CONFLICT, detail={"pid": params["pid"], "seq": seq})
        head = rows(D4)
        head_seq = head[0][0] if head else 0
        if params["read_seq"] > head_seq:
            return Outcome(Code.INVALID_VIEW, detail={"read_seq": params["read_seq"], "head_seq": head_seq})
        stale = rows(D5)
        if stale:
            return Outcome(Code.REJECTED_STALE, detail={"stale": [dict(zip(STALE_FIELDS, row)) for row in stale]})
        if rule.requires_decision:
            reason = decision_reason(graph, rule, params)
            if reason is not None:
                return Outcome(Code.REJECTED_DECISION, detail={"reason": reason})
    for probe in rule.guard_probes:
        if not rows(probe["cypher"]):
            return Outcome(Code.REJECTED_GUARD, detail={"guard": probe["name"]})
    return Outcome(Code.INDETERMINATE)
