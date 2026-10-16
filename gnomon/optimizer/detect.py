"""Detection rules D1-D4 and the candidate filter (master §16.5, §16.6).

The query text maps aliases to labels and relationship types and gives the WHERE predicates; the profiled tree gives
the selectivity. Supported predicate forms are `=`, `<`, `<=`, `>`, `>=` and `IN` on a bare property; a function of a
property, a NOT, or an OR across different properties produces no candidate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from gnomon.footprint.parser import tokenize
from gnomon.optimizer.config import OptConfig
from gnomon.optimizer.plan import EDGE_ARG, NODE_ARG, Plan

CLAUSE_END = frozenset({"MATCH", "OPTIONAL", "WITH", "RETURN", "ORDER", "UNWIND", "SKIP", "LIMIT", "UNION", "CALL"})
OPS = frozenset({"=", "<", "<=", ">", ">="})
VALUE_KINDS = frozenset({"param", "num", "str"})


@dataclass(frozen=True)
class Candidate:
    rule: str
    key: str
    entity: str
    label: str
    prop: str
    op: str = "create"


def candidate_of(rule: str, entity: str, label: str, prop: str, op: str = "create") -> Candidate:
    return Candidate(rule, f"{entity}:{label}:{prop}", entity, label, prop, op)


def aliases_of(toks) -> dict[str, tuple[str, str]]:
    """alias -> (entity, label or type) for the labelled node and relationship patterns of the query."""
    found: dict[str, tuple[str, str]] = {}
    for i in range(len(toks) - 4):
        a, b, c, d = toks[i:i + 4]
        if a.text in "([" and b.kind == "id" and c.text == ":" and d.kind == "id" and a.kind == "op":
            found[b.text] = ("node" if a.text == "(" else "edge", d.text)
    return found


def _is_value(toks) -> bool:
    if len(toks) == 1:
        return toks[0].kind in VALUE_KINDS
    return bool(toks) and toks[0].text == "[" and toks[-1].text == "]" and all(
        t.kind in VALUE_KINDS or t.text in ("[", "]", ",") for t in toks)


def _predicate(toks) -> tuple[str, str] | None:
    """(alias, prop) of `a.p OP value` or `value OP a.p`, else None."""
    if len(toks) >= 5 and toks[0].kind == "id" and toks[1].text == "." and toks[2].kind == "id"             and (toks[3].text in OPS or toks[3].upper == "IN") and _is_value(toks[4:]):
        return (toks[0].text, toks[2].text)
    return _reversed(toks)


def _reversed(toks):
    """value OP a.p: the property is the last three tokens, the operator the one before them."""
    if len(toks) >= 5 and toks[-4].text in OPS:
        a, dot, p = toks[-3:]
        if a.kind == "id" and dot.text == "." and p.kind == "id" and _is_value(toks[:-4]):
            return (a.text, p.text)
    return None


def where_predicates(toks) -> list[tuple[str, str]]:
    preds: list[tuple[str, str]] = []
    i, n = 0, len(toks)
    while i < n:
        if toks[i].kind == "id" and toks[i].upper == "WHERE":
            j, depth, body = i + 1, 0, []
            while j < n and toks[j].kind != "eof":
                t = toks[j]
                if t.kind == "op" and t.text in "([{":
                    depth += 1
                elif t.kind == "op" and t.text in ")]}":
                    depth -= 1
                if depth == 0 and t.kind == "id" and t.upper in CLAUSE_END:
                    break
                body.append(t)
                j += 1
            preds += _where_body(body)
            i = j
        else:
            i += 1
    return preds


def _split(body, word: str):
    parts, cur, depth = [], [], 0
    for t in body:
        if t.kind == "op" and t.text in "([{":
            depth += 1
        elif t.kind == "op" and t.text in ")]}":
            depth -= 1
        if depth == 0 and t.kind == "id" and t.upper == word:
            parts.append(cur)
            cur = []
        else:
            cur.append(t)
    parts.append(cur)
    return parts


def _where_body(body) -> list[tuple[str, str]]:
    if any(t.kind == "id" and t.upper in ("OR", "XOR") for t in _top_level(body)):
        found = [_predicate(p) for p in _split(body, "OR")]
        return [found[0]] if found[0] and all(f == found[0] for f in found) else []
    return [p for p in (_predicate(c) for c in _split(body, "AND")) if p]


def _top_level(body):
    depth = 0
    for t in body:
        if t.kind == "op" and t.text in "([{":
            depth += 1
        elif t.kind == "op" and t.text in ")]}":
            depth -= 1
        elif depth == 0:
            yield t


def _filter_chains(plan: Plan):
    """(top Filter, the operation below the chain) for every run of stacked Filters."""
    below_filter = {id(c) for n in plan.walk() if n.op == "Filter" for c in n.children}
    for node in plan.walk():
        if node.op == "Filter" and id(node) not in below_filter:
            low = node
            while low.children and low.children[0].op == "Filter":
                low = low.children[0]
            if low.children:
                yield node, low.children[0]


def candidates(text: str, plan: Plan, config: OptConfig) -> list[Candidate]:
    toks = tokenize(text)
    aliases = aliases_of(toks)
    preds = where_predicates(toks)
    found: dict[str, Candidate] = {}
    for top, src in _filter_chains(plan):
        if top.records is None or src.records is None or src.records == 0:
            continue
        if not top.records * config.theta_den < config.theta_num * src.records:
            continue
        if src.op == "Node By Label Scan" and src.arg and (m := NODE_ARG.search(src.arg)):
            for alias, prop in preds:
                if alias == m.group(1) and aliases.get(alias, ("", ""))[0] == "node":
                    c = candidate_of("D1", "node", m.group(2), prop)
                    found.setdefault(c.key, c)
        elif src.op == "Conditional Traverse" and src.arg and (m := EDGE_ARG.search(src.arg)):
            edge_only = all(aliases.get(a, ("", ""))[0] == "edge" for a, _ in preds)
            below = src.children[0].op if src.children else ""
            rule = "D3" if edge_only and below in ("Node By Label Scan", "All Node Scan") else "D2"
            for alias, prop in preds:
                if alias == m.group(1):
                    c = candidate_of(rule, "edge", m.group(2), prop)
                    found.setdefault(c.key, c)
    return [found[k] for k in sorted(found)]


def drop_candidates(indexes: Sequence[Mapping], plans: Sequence[Plan], state: Mapping[str, int], config: OptConfig,
                    shared: frozenset = frozenset()) -> tuple[list[Candidate], dict[str, int]]:
    """D4: an optimizer index no sampled plan scans, for drop_windows consecutive windows, is a drop candidate.

    EXPLAIN names the label or type of an index scan, not its property. When the label has another index (`shared`)
    a scan cannot be attributed, so the index counts as unused and the drop trial decides: if any plan changes
    without it, the candidate is discarded as `index_in_use` (DECISIONS D-63)."""
    used = {scan for p in plans for scan in p.index_scans()}
    counts: dict[str, int] = {}
    nominees: list[Candidate] = []
    for ix in sorted(indexes, key=lambda x: x["key"]):
        scope = (ix["entity"], ix["label"])
        if scope in used and scope not in shared:
            continue
        counts[ix["key"]] = state.get(ix["key"], 0) + 1
        if counts[ix["key"]] >= config.drop_windows:
            nominees.append(candidate_of("D4", ix["entity"], ix["label"], ix["prop"], "drop"))
    return nominees, counts


def filter_candidates(cands: Sequence[Candidate], entries: Sequence[Mapping], n_opt: int, now: int,
                      config: OptConfig, constraint_indexes: set[str]):
    """(kept, [(candidate, reason)]) per §16.6: exists, budget (creates), cooldown, off_limits (drops)."""
    by_key = {e["key"]: e for e in entries}
    kept: list[Candidate] = []
    dropped: list[tuple[Candidate, str]] = []
    for c in cands:
        e = by_key.get(c.key)
        reason = None
        if c.op == "create":
            if e and e["state"] in ("operational", "requested", "retiring"):
                reason = "exists"
            elif n_opt >= config.index_budget:
                reason = "budget"
            elif e and e["state"] in ("retired", "failed") and e.get("settled_pt", 0) + config.cooldown_ms > now:
                reason = "cooldown"
        else:
            if e is None or e.get("origin") == "genesis" or c.key in constraint_indexes:
                reason = "off_limits"
            elif e["state"] != "operational":
                reason = "not_operational"
            elif e.get("settled_pt", 0) + config.cooldown_ms > now:
                reason = "cooldown"
        (dropped.append((c, reason)) if reason else kept.append(c))
    return kept, dropped
