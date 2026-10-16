"""The kernel service (master §6.8, §6.9): submit → bind → compose → execute → outcome.

`bind` fills every parameter the agent may not choose: the pinned rule sha, the write keys (from the
rule's key templates and $p only), params_json = JCS($p) and the kernel constants. One GRAPH.QUERY
applies the proposal atomically; one row is APPLIED, zero rows are diagnosed (§6.10), and an error is
KERNEL_ERROR with nothing written (platform fact S4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import json
from dataclasses import dataclass, field

import redis

from gnomon.footprint import keys as key_templates
from gnomon.kernel import composer, diagnosis
from gnomon.kernel.jcs import JcsError, canonicalize
from gnomon.kernel.outcomes import Code, Outcome
from gnomon.kernel.registry import Registry, RuleDef

INF = 9007199254740991
ZERO64 = "0" * 64
MODES = ("live", "replay", "sim")
MAX_TTL_MS = 900_000  # Appendix C (D-21)
MAX_ATTEMPTS = 5
APPROVAL_TTL_MS = 900_000  # Appendix C: R16 bound
OPT_INDEX_BUDGET = 24  # Appendix C: M1 (IndexChange) optimizer-index budget
OPT_COOLDOWN_MS = 3_600_000  # Appendix C: M1 cooldown of a settled index key
# Appendix C: the closed role set, bound as $ROLES for M6 (DECISIONS D-12).
ROLES = ("kernel", "operator", "ingest", "triage", "investigator", "comms", "responder", "human", "scheduler",
         "dispatcher", "executive", "optimizer", "reconciler", "relay")


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


def wire_text(query: str) -> str:
    """The query text sent to the server: comments removed, so the text is plain ASCII (DECISIONS D-20).

    FalkorDB truncates a slow write's text at byte 2048 for its slow log and aborts the server when that byte
    falls inside a multi-byte UTF-8 character (FACTS S24). Kernel segments and rule bodies carry non-ASCII only
    in comments; parameters travel in the CYPHER header, which the slow log does not record.
    """
    out, i, n, quote = [], 0, len(query), ""
    while i < n:
        c = query[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(query[i + 1])
                i += 1
            elif c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
            out.append(c)
        elif query.startswith("//", i):
            j = query.find("\n", i)
            i = n if j < 0 else j
            continue
        elif query.startswith("/*", i):
            j = query.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return "\n".join(line.rstrip() for line in "".join(out).splitlines() if line.strip()) + "\n"


def instantiate_keys(templates: Sequence[str], p: Mapping, pid: str) -> list[str]:
    """Write keys from the pinned key templates and $p only (§7.3); see gnomon.footprint.keys (D-18)."""
    try:
        return key_templates.instantiate(templates, p, pid)
    except key_templates.KeyValueError as exc:
        raise SchemaRejected(str(exc)) from exc


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
    decision = None
    if rule.requires_decision and proposal.mode != "sim":  # sim mode composes without P1/E0 (master §15.4)
        if not isinstance(proposal.attach, str) or not proposal.attach:
            raise SchemaRejected("a decision rule needs its decision envelope in attach")
        try:
            decision = json.loads(proposal.attach)
        except ValueError as exc:
            raise SchemaRejected(f"attach is not JSON: {exc}") from exc
        if not isinstance(decision, dict):
            raise SchemaRejected("attach must be the decision envelope (a JSON object)")
    out = {
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
        "MAX_TTL_MS": MAX_TTL_MS,
        "MAX_ATTEMPTS": MAX_ATTEMPTS,
        "APPROVAL_TTL_MS": APPROVAL_TTL_MS,
        "OPT_INDEX_BUDGET": OPT_INDEX_BUDGET,
        "OPT_COOLDOWN_MS": OPT_COOLDOWN_MS,
    }
    if decision is not None:
        out["d"] = decision
    return out


class LintRejected(SchemaRejected):
    """A RegisterRule definition that fails L1–L8 (§5.5)."""

    def __init__(self, results: list[dict]) -> None:
        super().__init__("lint failed")
        self.results = results


def _lint_registration(proposal: Proposal) -> Proposal:
    """§9.13: lint an M3 definition before proposing it and stamp the report hash into def.lint_sha (D-18)."""
    from gnomon.footprint.lints import lint, rule_from_definition
    from gnomon.footprint.parser import ParseError

    p = proposal.p
    if not isinstance(p, Mapping) or not isinstance(p.get("def"), Mapping):
        raise SchemaRejected("RegisterRule params need a def map")
    try:
        report = lint(rule_from_definition(p["def"]))
    except ParseError as exc:
        raise SchemaRejected(f"definition does not parse: {exc}") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaRejected(f"definition is incomplete: {exc}") from exc
    if not report.ok:
        raise LintRejected(report.as_dict()["results"])
    stamped = {**p, "def": {**p["def"], "lint_sha": report.sha}}
    return dataclasses.replace(proposal, p=stamped)


@dataclass
class KernelService:
    graph: object
    registry: Registry
    _queries: dict[tuple, str] = field(default_factory=dict, repr=False)

    def _prepare(self, proposal: Proposal) -> tuple[RuleDef, dict]:
        try:
            rule = self.registry.get(proposal.rule)
        except KeyError as exc:
            raise SchemaRejected(str(exc.args[0])) from exc
        return rule, bind(rule, proposal)

    def submit(self, proposal: Proposal) -> Outcome:
        try:
            if proposal.rule == "RegisterRule":
                proposal = _lint_registration(proposal)
            rule, params = self._prepare(proposal)
        except LintRejected as exc:
            return Outcome(Code.SCHEMA_REJECTED, detail={"reason": "lint failed", "lints": exc.results})
        except SchemaRejected as exc:
            return Outcome(Code.SCHEMA_REJECTED, detail={"reason": str(exc)})
        sim = proposal.mode == "sim"
        query = self._queries.get((rule.sha, sim))
        if query is None:
            query = self._queries[(rule.sha, sim)] = wire_text(composer.compose(rule, sim))
        try:
            rows = self.graph.query(query, params).result_set
        except redis.ResponseError as exc:
            return Outcome(Code.KERNEL_ERROR, detail={"error": str(exc)})
        if rows:
            seq, digest, *decision = rows[0]  # E0 also returns the Decision id
            return Outcome(Code.APPLIED, seq=seq, hash=digest, detail={"decision": decision[0]} if decision else {})
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
