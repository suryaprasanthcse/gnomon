"""Replay engine (master §12.1): rebuild a tenant graph from its log, one recorded rewrite at a time.

    G = t:<tenant>:replay:<S> (or a named scratch key)
    bootstrap DDL on G, then for seq in 1..S:
        r  = the source's Rewrite at seq;  ru = the source's Rule with r.rule_sha
        apply ru's composed query to G with r's recorded inputs, in mode "replay" at (r.pt, r.lc)
        the applied (seq, hash) must equal r's, else DIVERGENCE(seq)

Replay passes the recorded params text, view keys and write keys, so P0 re-validates OCC against the
replayed KeyVersions and Q0 recreates CAUSED_BY exactly as the live run did.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

import redis

from gnomon.bootstrap import ddl
from gnomon.kernel import composer, diagnosis
from gnomon.kernel.registry import RuleDef
from gnomon.kernel.service import MESSAGE_PRELUDES, Proposal, SchemaRejected, bind, wire_text


class Divergence(RuntimeError):
    """Replay could not reproduce the recorded rewrite at `seq`.

    reason: "missing" (no rewrite or no rule for the step), "error" (the query or binding failed),
    "rejected" (the kernel refused the recorded proposal) or "mismatch" (applied with another seq or hash).
    """

    def __init__(self, seq: int, reason: str, detail: str = "", expected: Mapping | None = None,
                 actual: Mapping | None = None) -> None:
        super().__init__(f"DIVERGENCE at seq {seq}: {reason}{': ' + detail if detail else ''}")
        self.seq = seq
        self.reason = reason
        self.detail = detail
        self.expected = dict(expected or {})
        self.actual = dict(actual or {})

    def as_dict(self) -> dict:
        return {"seq": self.seq, "reason": self.reason, "detail": self.detail, "expected": self.expected,
                "actual": self.actual}


@dataclass(frozen=True)
class ReplayResult:
    key: str
    seq: int
    hash: str | None


def rule_from_node(props: Mapping) -> RuleDef:
    """The RuleDef a stored Rule node describes (its def_json fields, §5.1)."""
    return RuleDef(
        name=props["name"],
        version=int(props["version"]),
        cls=props["class"],
        layer=props["layer"],
        caller=tuple(props.get("caller") or ()),
        prelude=props["prelude"],
        body=props["body"],
        family=props.get("family"),
        requires_decision=bool(props.get("requires_decision", False)),
        gated=bool(props.get("gated", False)),
        evidence=bool(props.get("evidence", False)),
        reads=tuple(props.get("reads") or ()),
        writes=tuple(props.get("writes") or ()),
        key_templates=tuple(props.get("key_templates") or ()),
        view_templates=tuple(props.get("view_templates") or ()),
        guard_probes=tuple(json.loads(props.get("guard_probes_json") or "[]")),
        lint_sha=props.get("lint_sha"),
    )


def recorded_params(rule: RuleDef, r: Mapping) -> dict:
    """Query parameters for a recorded rewrite: the kernel constants from `bind`, the recorded inputs verbatim."""
    message = json.loads(r["attach"]) if rule.prelude in MESSAGE_PRELUDES else None  # the relay's $attach is JCS($m)
    params = bind(rule, Proposal(
        rule=r["rule"], p=json.loads(r["params"]), pid=r["id"], agent=r["agent"], read_seq=r["read_seq"],
        view_keys=tuple(r.get("view_keys") or ()), attach=r.get("attach"), mode="replay", rec_pt=r["pt"],
        rec_lc=r["lc"], m=message,
    ))
    params.update(rule=r["rule"], rule_sha=r["rule_sha"], params_json=r["params"],
                  write_keys=list(r.get("write_keys") or ()))
    return params


def _one(graph, query: str, params: Mapping) -> list:
    return graph.ro_query(query, dict(params)).result_set


def replay(db, client: redis.Redis, source: str, *, until: int | None = None, target: str | None = None) -> ReplayResult:
    """Replays `source` into `target` (default `<source>:replay:<until>`) up to seq `until` (default Head.seq)."""
    src = db.select_graph(source)
    if until is None:
        head = _one(src, "MATCH (h:Head) RETURN h.seq", {})
        if not head:
            raise Divergence(0, "missing", f"{source} has no Head")
        until = head[0][0]
    key = target or f"{source}:replay:{until}"
    if client.exists(key):
        client.execute_command("GRAPH.DELETE", key)
    graph = db.select_graph(key)
    ddl.apply(graph, client)

    queries: dict[str, str] = {}
    last_hash = None
    for seq in range(1, until + 1):
        rows = _one(src, "MATCH (r:Rewrite {seq: $seq}) RETURN properties(r)", {"seq": seq})
        if len(rows) != 1:
            raise Divergence(seq, "missing", f"{len(rows)} rewrites at seq {seq}")
        r = rows[0][0]
        rules = _one(src, "MATCH (x:Rule {sha: $sha}) RETURN properties(x)", {"sha": r["rule_sha"]})
        if len(rules) != 1:
            raise Divergence(seq, "missing", f"rule {r.get('rule')} {r['rule_sha']} is not in the source")
        expected = {"seq": r["seq"], "hash": r["hash"]}
        try:
            rule = rule_from_node(rules[0][0])
            params = recorded_params(rule, r)
            query = queries.get(rule.sha)
            if query is None:
                query = queries[rule.sha] = wire_text(composer.compose(rule))
            out = graph.query(query, params).result_set
        except (redis.ResponseError, SchemaRejected, KeyError, TypeError, ValueError) as exc:
            raise Divergence(seq, "error", str(exc), expected) from exc
        if not out:
            outcome = diagnosis.diagnose(graph, rule, params)
            raise Divergence(seq, "rejected", f"{outcome.code} {outcome.detail}", expected)
        actual = {"seq": out[0][0], "hash": out[0][1]}
        if actual != expected:
            raise Divergence(seq, "mismatch", "", expected, actual)
        last_hash = actual["hash"]
    return ReplayResult(key, until, last_hash)
