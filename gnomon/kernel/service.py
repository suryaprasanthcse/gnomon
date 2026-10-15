"""The kernel service (master §6.8, §6.9): submit → bind → compose → execute → outcome.

`bind` fills every parameter the agent may not choose: the pinned rule sha, the write keys (from the
rule's key templates and $p only), params_json = JCS($p) and the kernel constants. One GRAPH.QUERY
applies the proposal atomically; one row is APPLIED, zero rows are diagnosed (§6.10), and an error is
KERNEL_ERROR with nothing written (platform fact S4).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import redis

from gnomon.kernel import composer, diagnosis
from gnomon.kernel.jcs import JcsError, canonicalize
from gnomon.kernel.outcomes import Code, Outcome
from gnomon.kernel.registry import Registry, RuleDef

INF = 9007199254740991
ZERO64 = "0" * 64
MODES = ("live", "replay", "sim")
# Appendix C: the closed role set, bound as $ROLES for M6 (DECISIONS D-12).
ROLES = ("kernel", "operator", "ingest", "triage", "investigator", "comms", "responder", "human", "scheduler",
         "dispatcher", "executive", "optimizer", "reconciler", "relay")

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_FIELD = r"[A-Za-z_]\w*"
_SCALAR = re.compile(rf"p\.({_FIELD}(?:\.{_FIELD})*)")
_LIST = re.compile(rf"p\.({_FIELD})\[\](?:\.({_FIELD}))?")


class SchemaRejected(ValueError):
    """A proposal that fails validation before composition (SCHEMA_REJECTED)."""


@dataclass(frozen=True)
class Proposal:
    rule: str
    p: object
    pid: str
    agent: str
    read_seq: int
    view_keys: Sequence[str] = ()
    attach: str | None = None
    mode: str = "live"
    rec_pt: int | None = None
    rec_lc: int | None = None


def _render(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def instantiate_keys(templates: Sequence[str], p: Mapping, pid: str) -> list[str]:
    """Key templates (§5.4) → keys. Segments: literal, `*`, {pid}, {p.f}, {p.f.g}, {p.list[]}, {p.list[].f}.

    A missing or null value yields no key; list placeholders yield one key per non-null element.
    Keys keep template order, then list order, without duplicates.
    """
    keys: list[str] = []
    for template in templates:
        if _PLACEHOLDER.sub("", template).count("{") or _PLACEHOLDER.sub("", template).count("}"):
            raise ValueError(f"bad key template {template!r}: unbalanced braces")
        # Each placeholder becomes a list of alternatives; a list placeholder may appear once per template.
        pieces: list[list[str] | str] = []
        position, lists = 0, 0
        for match in _PLACEHOLDER.finditer(template):
            pieces.append(template[position:match.start()])
            pieces.append(_values(template, match.group(1), p, pid))
            lists += bool(_LIST.fullmatch(match.group(1)))
            position = match.end()
        pieces.append(template[position:])
        if lists > 1:
            raise ValueError(f"bad key template {template!r}: more than one list placeholder")
        expansions = [""]
        for piece in pieces:
            if isinstance(piece, str):
                expansions = [e + piece for e in expansions]
            else:
                expansions = [e + v for e in expansions for v in piece]
        for key in expansions:
            if key not in keys:
                keys.append(key)
    return keys


def _values(template: str, placeholder: str, p: Mapping, pid: str) -> list[str]:
    if placeholder == "pid":
        return [pid]
    if scalar := _SCALAR.fullmatch(placeholder):
        value: object = p
        for part in scalar.group(1).split("."):  # nested paths such as {p.def.name} (D-12)
            value = value.get(part) if isinstance(value, Mapping) else None
        return [] if value is None else [_render(value)]
    if listed := _LIST.fullmatch(placeholder):
        items = p.get(listed.group(1)) or []
        if not isinstance(items, (list, tuple)):
            raise SchemaRejected(f"key template {template!r}: p.{listed.group(1)} is not a list")
        attr = listed.group(2)
        if attr is None:
            return [_render(v) for v in items if v is not None]
        return [_render(v[attr]) for v in items if isinstance(v, Mapping) and v.get(attr) is not None]
    raise ValueError(f"bad key template {template!r}: unknown placeholder {{{placeholder}}}")


def _is_nat(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def bind(rule: RuleDef, proposal: Proposal) -> dict:
    """All query parameters for one proposal (§6.8). Raises SchemaRejected for malformed input."""
    if not isinstance(proposal.p, Mapping):
        raise SchemaRejected("params must be a map")
    if not isinstance(proposal.pid, str) or not proposal.pid:
        raise SchemaRejected("pid must be a non-empty string")
    if not isinstance(proposal.agent, str) or not proposal.agent:
        raise SchemaRejected("agent must be a non-empty string")
    if not _is_nat(proposal.read_seq):
        raise SchemaRejected("read_seq must be a non-negative integer")
    if not all(isinstance(k, str) for k in proposal.view_keys):
        raise SchemaRejected("view keys must be strings")
    if proposal.mode not in MODES:
        raise SchemaRejected(f"mode must be one of {MODES}")
    try:
        params_json = canonicalize(proposal.p)
    except JcsError as exc:
        raise SchemaRejected(f"params are not canonical JSON: {exc}") from exc
    return {
        "rule": rule.name,
        "rule_sha": rule.sha,
        "agent": proposal.agent,
        "pid": proposal.pid,
        "p": dict(proposal.p),
        "params_json": params_json,
        "attach": proposal.attach,
        "read_seq": proposal.read_seq,
        "view_keys": list(proposal.view_keys),
        "write_keys": instantiate_keys(rule.key_templates, proposal.p, proposal.pid),
        "mode": proposal.mode,
        "rec_pt": proposal.rec_pt,
        "rec_lc": proposal.rec_lc,
        "INF": INF,
        "ZERO64": ZERO64,
        "ROLES": list(ROLES),
    }


@dataclass
class KernelService:
    graph: object
    registry: Registry
    _queries: dict[str, str] = field(default_factory=dict, repr=False)

    def _prepare(self, proposal: Proposal) -> tuple[RuleDef, dict]:
        try:
            rule = self.registry.get(proposal.rule)
        except KeyError as exc:
            raise SchemaRejected(str(exc.args[0])) from exc
        return rule, bind(rule, proposal)

    def submit(self, proposal: Proposal) -> Outcome:
        try:
            rule, params = self._prepare(proposal)
        except SchemaRejected as exc:
            return Outcome(Code.SCHEMA_REJECTED, detail={"reason": str(exc)})
        query = self._queries.get(rule.sha)
        if query is None:
            query = self._queries[rule.sha] = composer.compose(rule)
        try:
            rows = self.graph.query(query, params).result_set
        except redis.ResponseError as exc:
            return Outcome(Code.KERNEL_ERROR, detail={"error": str(exc)})
        if rows:
            seq, digest = rows[0]
            return Outcome(Code.APPLIED, seq=seq, hash=digest)
        return diagnosis.diagnose(self.graph, rule, params)

    def diagnose(self, proposal: Proposal) -> Outcome:
        rule, params = self._prepare(proposal)
        return diagnosis.diagnose(self.graph, rule, params)


class KernelHandle:
    """A tenant's kernel for callers and tests: submit proposals, read the graph, read Head."""

    def __init__(self, graph, registry: Registry) -> None:
        self.graph = graph
        self.registry = registry
        self.service = KernelService(graph, registry)

    def head(self) -> dict:
        rows = self.graph.ro_query("MATCH (h:Head) RETURN properties(h)").result_set
        return dict(rows[0][0]) if rows else {}

    def read(self, cypher: str, params: Mapping | None = None) -> list:
        return self.graph.ro_query(cypher, dict(params or {})).result_set

    def submit(self, rule: str, p: object = None, *, pid: str, agent: str = "kernel", read_seq: int | None = None,
               view_keys: Sequence[str] = (), attach: str | None = None, mode: str = "live",
               rec_pt: int | None = None, rec_lc: int | None = None) -> Outcome:
        if read_seq is None:
            read_seq = self.head().get("seq", 0)
        return self.service.submit(Proposal(
            rule=rule, p={} if p is None else p, pid=pid, agent=agent, read_seq=read_seq, view_keys=tuple(view_keys),
            attach=attach, mode=mode, rec_pt=rec_pt, rec_lc=rec_lc,
        ))
