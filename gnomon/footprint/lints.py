"""Lints L1–L8 (master §5.5), applied before a rule is registered; the report's hash becomes `lint_sha`.

L1 unguarded write   every instance key the rule writes is protected by a guard that reads what it covers
L2 non-determinism   no timestamp()/rand()/randomUUID(); no positional pick from an unordered collect()
                     unless the collected variable is unique-anchored or its count is guarded
L3 single-row body   every MATCH part is anchored (bound variable or indexed property), and the body cannot
                     end with more than one row
L4 completeness      the declared reads/writes contain everything the parser finds
L5 key provenance    key templates use only $p fields, {pid}, or {m.<field>} in P2/P3 rules
L6 write ordering    no MATCH / OPTIONAL MATCH / UNWIND / WHERE after the first write clause
L7 same-SET          no property both read and written in one SET
L8 decision flags    gated ⇒ requires_decision; evidence = requires_decision; action ⇒ requires_decision

Coverage (L1, also used by the conflict matrix): an instance key rooted at X protects X. A `…:state` key
covers X's non-identity properties, so reading only X's identity (its id) does not guard it; any other
instance key covers every element of X. A creation of X is also guarded by an identity read of any label
created with it (the "is this uid free?" check). Genesis (prelude PG) validates in its prelude, so L1 does
not apply to its body.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from gnomon.footprint import keys as key_templates
from gnomon.footprint.parser import Footprint, extract
from gnomon.kernel.jcs import canonicalize
from gnomon.kernel.registry import RuleDef

IDENTITY = frozenset({"id", "uid", "fid", "key", "sha", "pid", "source_id", "rel", "aid", "cid", "eid", "remote_id"})
NONDETERMINISTIC = frozenset({"timestamp", "rand", "randomuuid"})
LINT_IDS = tuple(f"L{n}" for n in range(1, 9))


class LintFailed(ValueError):
    """A definition that does not pass L1–L8."""


@dataclass
class LintResult:
    id: str
    ok: bool
    messages: list[str] = field(default_factory=list)


@dataclass
class LintReport:
    rule_sha: str
    results: list[LintResult]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def as_dict(self) -> dict:
        return {"rule_sha": self.rule_sha,
                "results": [{"id": r.id, "ok": r.ok, "messages": list(r.messages)} for r in self.results]}

    @property
    def sha(self) -> str:
        return hashlib.sha256(canonicalize(self.as_dict()).encode("utf-8")).hexdigest()


def owner(element: str) -> str:
    return element.split(".", 1)[0]


def prop(element: str) -> str | None:
    return element.split(".", 1)[1] if "." in element else None


def fresh_roots(rule: RuleDef) -> set[str]:
    """Roots of keys whose only placeholder is {pid}: an instance created under such a key is new by construction,
    because P0 refuses a proposal whose pid is already in the log (D-22b)."""
    out = set()
    for template in rule.key_templates:
        placeholders = re.findall(r"\{([^{}]*)\}", template)
        if placeholders and all(ph == "pid" for ph in placeholders):
            out.add(key_templates.root(template))
    return out


def creation_guarded(element: str, f: Footprint, guard_reads: Iterable[str]) -> bool:
    """A created element counts as guarded when the body checks the identity of something created with it."""
    groups = [g for g in f.created if element in g]
    return any(owner(r) in g and prop(r) in IDENTITY for g in groups for r in guard_reads)


def l1(rule: RuleDef, f: Footprint) -> list[str]:
    if rule.prelude == "PG":
        return []
    unguarded = []
    for template in rule.key_templates:
        coverage = key_templates.covers(template)
        if coverage not in ("state", "all"):
            continue
        x = key_templates.root(template)
        written = sorted(w for w in f.writes if owner(w) == x)
        if not written:
            continue
        if coverage == "state":
            covered = [w for w in written if prop(w) is None or prop(w) not in IDENTITY]
            reads = [r for r in f.guard_reads if owner(r) == x and prop(r) is not None and prop(r) not in IDENTITY]
        else:
            covered = written
            reads = [r for r in f.guard_reads if owner(r) == x]
        if reads or (x in written and (creation_guarded(x, f, f.guard_reads) or x in fresh_roots(rule))):
            continue
        unguarded.append(f"{', '.join(covered)} ({template})")
    return [f"L1 unguarded write: {', '.join(unguarded)}"] if unguarded else []


def l2(rule: RuleDef, f: Footprint) -> list[str]:
    messages = [f"L2 non-deterministic call {name}()" for name in sorted(f.calls & NONDETERMINISTIC)]
    for pick in f.picks:
        if pick.anchored or (pick.count_alias and pick.count_alias in f.where_names):
            continue
        messages.append(f"L2 {pick.alias} picks one element of an unordered collect({pick.var}) without a count "
                        "guard or a unique anchor")
    return messages


def l3(rule: RuleDef, f: Footprint) -> list[str]:
    messages = [f"L3 {m.clause} {m.description} is not anchored on a bound variable or an indexed property"
                for m in f.matches if not m.anchored]
    if f.may_end_with_many_rows:
        messages.append("L3 the body can end with more than one row (add an aggregation or anchor on unique keys)")
    return messages


def l4(rule: RuleDef, f: Footprint) -> list[str]:
    messages = []
    for kind, found, declared in (("reads", f.reads, rule.reads), ("writes", f.writes, rule.writes)):
        missing = sorted(set(found) - set(declared))
        if missing:
            messages.append(f"L4 undeclared {kind}: {', '.join(missing)}")
    return messages


def l5(rule: RuleDef, f: Footprint) -> list[str]:
    return [f"L5 {problem}" for t in rule.key_templates for problem in key_templates.template_problems(t, rule.prelude)]


def l6(rule: RuleDef, f: Footprint) -> list[str]:
    return [f"L6 {kind} after the first write clause" for kind in f.reads_after_write]


def l7(rule: RuleDef, f: Footprint) -> list[str]:
    return [f"L7 {element} is read and written in one SET" for element in sorted(f.set_conflicts)]


def l8(rule: RuleDef, f: Footprint) -> list[str]:
    messages = []
    if rule.gated and not rule.requires_decision:
        messages.append("L8 gated requires requires_decision")
    if rule.evidence != rule.requires_decision:
        messages.append("L8 evidence must equal requires_decision")
    if rule.cls == "action" and not rule.requires_decision:
        messages.append("L8 an action rule requires requires_decision")
    return messages


CHECKS = {"L1": l1, "L2": l2, "L3": l3, "L4": l4, "L5": l5, "L6": l6, "L7": l7, "L8": l8}


def lint(rule: RuleDef) -> LintReport:
    """Run L1–L8. Raises parser.ParseError when the body is not in the accepted dialect."""
    f = extract(rule.body)
    results = []
    for lint_id in LINT_IDS:
        messages = CHECKS[lint_id](rule, f)
        results.append(LintResult(lint_id, not messages, messages))
    return LintReport(rule.sha, results)


def rule_from_definition(d: Mapping) -> RuleDef:
    """A RuleDef from an M3 `$p.def` map (RuleDef.to_param's shape)."""
    return RuleDef(
        name=d["name"], version=int(d["version"]), cls=d["class"], layer=d["layer"], caller=tuple(d["caller"]),
        prelude=d["prelude"], body=d["body"], family=d.get("family"),
        requires_decision=bool(d.get("requires_decision")), gated=bool(d.get("gated")),
        evidence=bool(d.get("evidence")), reads=tuple(d.get("reads") or ()), writes=tuple(d.get("writes") or ()),
        key_templates=tuple(d.get("key_templates") or ()), view_templates=tuple(d.get("view_templates") or ()),
        guard_probes=tuple({"name": g["name"], "cypher": g["cypher"]} for g in d.get("guard_probes") or ()),
        lint_sha=d.get("lint_sha"),
    )
