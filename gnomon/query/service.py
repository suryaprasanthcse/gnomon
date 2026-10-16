"""The query service (master §10): the only way an agent reads, and the gate in front of the kernel's `propose`.

Every binding read is one GRAPH.RO_QUERY whose first clause is `MATCH (h:Head)`, so its rows and `read_seq` come from
one snapshot (§7.2). Reads are recorded in the caller's session. `propose` resolves the cited reads into
`read_seq = min`, `view_keys = union` and the id-provenance set (§10.1), refuses ids no cited read showed, builds the
parameters of enumerated rules from the chosen tuple (so ids cannot be invented, §10.3), and hands the result to the
kernel service. It never writes anything itself.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from gnomon.footprint import keys as key_templates
from gnomon.kernel.outcomes import Code, Outcome
from gnomon.kernel.service import INF, MAX_TTL_MS, KernelHandle, wire_text
from gnomon.query.session import Read, Session, UnknownRead, combine

HERE = Path(__file__).parent
TEXT_MAX = 2000  # §10.3 rule 4: free-text fields are length-limited
DEFAULT_TTL_MS = 300_000
CAUSE_WINDOW_MS = 86_400_000  # Appendix C
SEQ_COL = "read_seq"

UID_PREFIX = {"svc": "Service", "team": "Team", "dep": "Deploy", "inc": "Incident", "task": "Task", "agent": "Agent",
              "msg": "Message", "ext": "ExternalService", "al": "Alert"}
AS_OF = {  # query -> (required args, {arg: type})
    "Q1": {"svc": str, "T": int, "S": int}, "Q2": {"svc": str}, "Q3": {"svc": str, "S": int},
    "Q4": {"team": str, "T": int}, "Q5": {"T1": int, "T2": int}, "Q6": {"incident": str}, "Q7": {"T": int},
}
# column -> uid prefix, for ids the as-of rows show as bare ids
COLUMN_UID = {"team": "team:", "service": "svc:"}
ARG_UID = {"svc": "svc:", "team": "team:"}


class QueryError(ValueError):
    """A read request that is malformed (unknown query, missing or unexpected argument)."""


class Reject(Exception):
    """A proposal refused before composition; becomes SCHEMA_REJECTED."""

    def __init__(self, reason: str, **detail: object) -> None:
        super().__init__(reason)
        self.detail = {"reason": reason, **detail}


@dataclass(frozen=True)
class Enablement:
    """An agent-callable enablement query: its file, its arguments and how a tuple becomes rule parameters."""

    file: str
    args: tuple[str, ...] = ()
    caller_param: bool = False  # the query takes $agent (always the session's agent)
    knobs: frozenset[str] = frozenset()  # numeric choices an agent may still make with a tuple
    text_field: str | None = None  # the rule parameter that carries the free text
    tupled: bool = True  # False: open-parameter rule (the read lists citeable facts, §10.2 last paragraph)
    proposable: bool = True  # False: readable through list_actions, but only the Executive may propose it (§15)


ENABLEMENT = {
    "OpenIncident": Enablement("en_r4"),
    "ClaimTask": Enablement("en_r5", caller_param=True, knobs=frozenset({"ttl_ms"})),
    "CompleteTask": Enablement("en_r6", caller_param=True, text_field="result"),
    "Handoff": Enablement("en_r7", caller_param=True, knobs=frozenset({"ttl_ms"})),
    "PostUpdate": Enablement("en_r9", args=("incident",), text_field="body", tupled=False),
    "ProposeCause": Enablement("en_r10", args=("incident",), knobs=frozenset({"confidence", "cites"})),
    "ResolveIncident": Enablement("en_r11"),
    # §15.12 action enablement: read by agents to nominate, run by the Executive on its base snapshot; never proposed by agents
    "IsolateService": Enablement("en_r12", args=("incident",), tupled=False, proposable=False),
    "FailoverService": Enablement("en_r13", args=("incident",), tupled=False, proposable=False),
    "RollbackDeploy": Enablement("en_r14", args=("incident",), tupled=False, proposable=False),
    "EscalateIncident": Enablement("en_r15", args=("incident",), tupled=False, proposable=False),
    "RestoreService": Enablement("en_r18", args=("incident",), tupled=False, proposable=False),
}
ACTION_RULES = frozenset(r for r, spec in ENABLEMENT.items() if not spec.proposable)
OPEN_PARAMETER = {"PostUpdate"}
OPEN_FAMILIES = ("AssertFact.", "RetractFact.")
REFERENCE_FIELDS = ("s", "o", "old_o", "fid")  # entity ids and fids an open-parameter proposal may name
POST_UPDATE_PARAMS = frozenset({"incident", "message_id", "cites"})


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _query_text(directory: str, name: str) -> str:
    return wire_text((HERE / directory / f"{name}.cypher").read_text(encoding="utf-8"))


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def _ids_of_rows(rows: Sequence[Mapping]) -> set[str]:
    ids = set()
    for row in rows:
        for column, value in row.items():
            if isinstance(value, str):
                ids.add(value)
                if column in COLUMN_UID:
                    ids.add(COLUMN_UID[column] + value)
    return ids


class QueryService:
    """Reads for one tenant graph. In `replay` and `sim` mode proposals take their time from `clock` (contract C8)."""

    def __init__(self, kernel: KernelHandle, *, mode: str = "live", clock: Callable[[], int] | None = None,
                 telemetry=None) -> None:
        if mode != "live" and clock is None:
            raise ValueError(f"mode {mode!r} needs a clock")
        self.kernel = kernel
        self.mode = mode
        self.clock = clock
        self.telemetry = telemetry  # optional workload telemetry (§16.2): one entry per agent read
        self._last: tuple | None = None
        self._read_ids = itertools.count(1)
        self._texts: dict[tuple[str, str], str] = {}

    # ── plumbing
    def session(self, agent: str) -> Session:
        return Session(agent)

    def _text(self, directory: str, name: str) -> str:
        key = (directory, name)
        if key not in self._texts:
            self._texts[key] = _query_text(directory, name)
        return self._texts[key]

    def _run(self, directory: str, name: str, params: Mapping | None = None) -> tuple[list[str], list[list]]:
        full = {"INF": INF, "CAUSE_WINDOW_MS": CAUSE_WINDOW_MS, **(params or {})}
        text = self._text(directory, name)
        result = self.kernel.graph.ro_query(text, full)
        self._last = (text, full, result.run_time_ms, len(result.result_set))
        names = [h[1] if isinstance(h, (list, tuple)) else h for h in result.header]
        return names, result.result_set

    def _rows(self, directory: str, name: str, params: Mapping | None = None) -> tuple[int, list[dict]]:
        """(read_seq, rows without the read_seq column). An empty result carries no seq, so the Head seq is read
        first and the query repeated: the seq claimed is then never newer than the state the rows describe."""
        names, rows = self._run(directory, name, params)
        if not rows:
            seq = self.kernel.head().get("seq", 0)
            names, rows = self._run(directory, name, params)
            if not rows:
                return seq, []
        at = names.index(SEQ_COL)
        seqs = {r[at] for r in rows}
        dicts = [{n: v for n, v in zip(names, r) if n != SEQ_COL} for r in rows]
        return min(seqs), dicts

    def _record(self, session: Session, kind: str, seq: int, keys: Sequence[str], ids: set[str], rows=(), **extra) -> Read:
        read = Read(read_id=f"rd-{next(self._read_ids)}", read_seq=seq, kind=kind, keys=tuple(sorted(set(keys))),
                    ids=frozenset(ids), rows=list(rows), **extra)
        if self.telemetry is not None and self._last is not None:
            text, params, internal_ms, n_rows = self._last
            self.telemetry.record(text, params, internal_ms, n_rows, session.agent)
        return session.record(read)

    # ── binding reads (§10.4, §4.3)
    def read_entity(self, session: Session, uid: str) -> Read:
        _check_str("uid", uid)
        seq, rows = self._rows("reads", "rd_entity", {"uid": uid})
        keys: list[str] = []
        if rows:
            labels = [label for label in rows[0]["labels"] if label != "Entity"]
            keys = [f"{labels[0]}:{rows[0]['props'].get('id')}:state"] if labels else []
        elif ":" in uid and uid.split(":", 1)[0] in UID_PREFIX:
            prefix, ident = uid.split(":", 1)
            keys = [f"{UID_PREFIX[prefix]}:{ident}:state"]  # the key a later creation would write
        return self._record(session, "read_entity", seq, keys, {uid} if rows else set(), rows)

    def facts(self, session: Session, uid: str) -> Read:
        _check_str("uid", uid)
        seq, rows = self._rows("reads", "rd_facts", {"uid": uid})
        rels = [ft["rel"] for ft in self.kernel.registry.fact_types()]
        keys = [f"{rel}:{uid}:*" for rel in rels] + [f"{rel}:*:{uid}" for rel in rels]
        ids = {uid} | {r["other"] for r in rows} | {r["fid"] for r in rows}
        return self._record(session, "facts", seq, keys, ids, rows)

    def as_of(self, session: Session, query: str, args: Mapping | None = None) -> Read:
        spec = AS_OF.get(query)
        if spec is None:
            raise QueryError(f"unknown as-of query {query!r}; known: {', '.join(AS_OF)}")
        args = dict(args or {})
        if set(args) != set(spec):
            raise QueryError(f"{query} takes exactly {sorted(spec)}; got {sorted(args)}")
        for name, kind in spec.items():
            ok = _is_int(args[name]) if kind is int else isinstance(args[name], str)
            if not ok:
                raise QueryError(f"{query} argument {name} must be {kind.__name__}")
        seq, rows = self._rows("reads", query.lower(), args)
        if query in ("Q1", "Q2", "Q3"):
            keys = [f"OWNED_BY:svc:{args['svc']}:*"]
        elif query in ("Q4", "Q5"):
            keys = ["OWNED_BY:*"]
        elif query == "Q6":
            keys = [f"OWNED_BY:svc:{r['service']}:*" for r in rows]
        else:
            keys = ["DEPENDS_ON:*"]
        ids = _ids_of_rows(rows) | {ARG_UID[a] + v for a, v in args.items() if a in ARG_UID}
        if query == "Q7":
            ids |= {r["from"] for r in rows} | {r["to"] for r in rows}
        return self._record(session, f"as_of.{query}", seq, keys, ids, rows)

    def incident_status(self, session: Session, incident: str) -> Read:
        _check_str("incident", incident)
        seq, raw = self._rows("reads", "rd_incident", {"incident": incident})
        rows: list[dict] = []
        ids: set[str] = set()
        if raw:
            rows.append({"incident": incident, "state": raw[0]["state"], "service": raw[0]["service"]})
            ids |= {f"inc:{incident}", incident, f"svc:{raw[0]['service']}"}
            tasks: dict[str, dict] = {}
            for r in raw:
                if r["task"] is None:
                    continue
                live = r["closed_seq"] is None and r["holder"] is not None
                entry = tasks.setdefault(r["task"], {"task": r["task"], "state": r["task_state"], "holder": None,
                                                     "lease_until": None})
                if live:
                    entry["holder"], entry["lease_until"] = r["holder"], r["lease_until"]
            rows.extend(tasks[t] for t in sorted(tasks))
            ids |= {f"task:{t}" for t in tasks} | set(tasks)
        return self._record(session, "incident_status", seq, [f"Incident:{incident}:state", "Task:*"], ids, rows)

    def list_actions(self, session: Session, rule: str, args: Mapping | None = None) -> Read:
        spec = ENABLEMENT.get(rule)
        if spec is None:
            raise QueryError(f"{rule!r} has no enablement query for agents; known: {', '.join(ENABLEMENT)}")
        args = dict(args or {})
        if set(args) != set(spec.args):
            raise QueryError(f"{rule} enablement takes exactly {list(spec.args)}; got {sorted(args)}")
        for name in spec.args:
            _check_str(name, args[name])
        params = dict(args)
        if spec.caller_param:
            params["agent"] = session.agent
        seq, rows = self._rows("enablement", spec.file, params)
        if rule == "PostUpdate":
            rows.sort(key=lambda r: (r["rel"], r["fid"]))
        try:
            view = self.kernel.registry.get(rule).view_templates
        except KeyError:
            raise QueryError(f"rule {rule!r} is not registered in this tenant") from None
        base = key_templates.instantiate(view, dict(args), "")  # literal (phantom) keys and the args' own keys
        tuples, tuple_keys, ids = [], [], set(args.values()) | {f"inc:{v}" for v in args.values()}
        for i, row in enumerate(rows):
            tuple_ = {"index": i, **row}
            keys = key_templates.instantiate(view, self._tuple_params(rule, tuple_, session.agent, args), "")
            if rule == "PostUpdate":
                keys.append(f"{row['rel']}:{row['s']}:*")
            if rule == "ProposeCause":
                keys.append(f"DEPLOYED:{row['service']}:*")
            tuples.append(tuple_)
            tuple_keys.append(tuple(sorted({*keys, *base})))
            ids |= _ids_of_rows([row]) | ({row["s"], row["o"]} if rule == "PostUpdate" else set())
        keys = set(base).union(*map(set, tuple_keys))
        return self._record(session, f"list_actions.{rule}", seq, keys, ids, (), rule=rule, tuples=tuples,
                            tuple_keys=tuple_keys)

    def agent_info(self, agent: str) -> dict | None:
        rows = self.kernel.read("MATCH (a:Agent {id: $agent}) OPTIONAL MATCH (a)-[:HAS_SKILL]->(s:Skill) "
                                "RETURN a.id, a.role, collect(s.id)", {"agent": agent})
        if not rows:
            return None
        return {"agent": rows[0][0], "role": rows[0][1], "skills": sorted(rows[0][2])}

    # ── advisory reads (§10.5): no read id, no keys, nothing to cite
    def neighborhood(self, svc: str, depth: int) -> dict:
        _check_str("svc", svc)
        _check_range("depth", depth, 1, 8)
        _, down = self._run("algorithms", "al_neighborhood", {"svc": svc, "depth": depth})
        up_text = self._text("algorithms", "al_neighborhood_up").replace("__DEPTH__", str(depth))
        up = self.kernel.graph.ro_query(up_text, {"svc": svc}).result_set
        return {"downstream": {"nodes": sorted(down[0][0]) if down else [], "fids": sorted(down[0][1]) if down else []},
                "upstream": {"nodes": sorted(up[0][0]) if up else [], "fids": sorted(up[0][1]) if up else []}}

    def _scored(self, name: str, params: dict) -> list[dict]:
        _, rows = self._run("algorithms", name, params)
        return [{"service": r[0], "score": r[1]} for r in rows]

    def rank_critical(self, k: int) -> list[dict]:
        _check_range("k", k, 1, 1000)
        return self._scored("al_rank_critical", {"k": k})

    def brokers(self, k: int, sampling: int) -> list[dict]:
        _check_range("k", k, 1, 1000)
        _check_range("sampling", sampling, 1, 100_000)
        return self._scored("al_brokers", {"k": k, "sampling": sampling})

    def _grouped(self, name: str, label: str) -> list[dict]:
        _, rows = self._run("algorithms", name)
        return [{label: r[0], "services": sorted(r[1])} for r in rows]

    def failure_domains(self) -> list[dict]:
        return self._grouped("al_domains", "component")

    def communities(self) -> list[dict]:
        return self._grouped("al_communities", "community")

    def dependency_path(self, source: str, target: str, max_len: int) -> dict | None:
        _check_str("source", source)
        _check_str("target", target)
        _check_range("max_len", max_len, 1, 16)
        _, rows = self._run("algorithms", "al_path", {"from": source, "to": target, "max_len": max_len})
        return {"nodes": rows[0][0], "fids": rows[0][1]} if rows else None

    def why(self, uid: str | None = None, pid: str | None = None) -> dict | None:
        from gnomon.audit.runner import why_entity

        if uid is not None:
            _check_str("uid", uid)
            return why_entity(self.kernel.graph, uid)
        if pid is None:
            raise QueryError("why needs a uid or a pid")
        _check_str("pid", pid)
        _, rows = self._run("reads", "rd_why", {"pid": pid})
        if not rows:
            return None
        pid_, seq, rule, agent, causes = rows[0]
        return {"pid": pid_, "seq": seq, "rule": rule, "agent": agent, "causes": sorted(c for c in causes if c)}

    # ── propose (§10.1 rules 3-4, §10.3)
    def propose(self, session: Session, rule: str, *, pid: str, tuple_index: int | None = None,
                params: Mapping | None = None, text: str | None = None, cite_reads: Sequence[str] = ()) -> Outcome:
        try:
            request = self._assemble(session, rule, pid, tuple_index, params, text, list(cite_reads or ()))
        except Reject as exc:
            return Outcome(Code.SCHEMA_REJECTED, detail=exc.detail)
        p, read_seq, view_keys = request
        rec_pt = self.clock() if self.mode != "live" and self.clock else None
        return self.kernel.submit(rule, p, pid=pid, agent=session.agent, read_seq=read_seq, view_keys=view_keys,
                                  mode=self.mode, rec_pt=rec_pt, rec_lc=0 if rec_pt is not None else None)

    def _assemble(self, session, rule, pid, tuple_index, params, text, cite_reads):
        if not isinstance(rule, str) or not ((rule in ENABLEMENT and ENABLEMENT[rule].proposable)
                                             or rule.startswith(OPEN_FAMILIES)):
            raise Reject(f"rule {rule!r} is not proposable by agents")
        if not isinstance(pid, str) or not pid:
            raise Reject("pid must be a non-empty string")
        if text is not None and (not isinstance(text, str) or len(text) > TEXT_MAX):
            raise Reject(f"text must be a string of at most {TEXT_MAX} characters")
        if params is not None and not isinstance(params, Mapping):
            raise Reject("params must be a map")
        if tuple_index is not None and (not _is_int(tuple_index) or tuple_index < 0):
            raise Reject("tuple_index must be a non-negative integer")
        try:
            reads = session.resolve(cite_reads)
        except UnknownRead as exc:
            raise Reject(f"unknown read {exc.args[0]!r}: cite only reads made in this session") from exc
        combined = combine(reads)
        if combined is None:
            raise Reject("a proposal must cite at least one read")
        spec = ENABLEMENT.get(rule)
        if spec is not None and spec.tupled:
            return self._from_tuple(session, rule, spec, tuple_index, params, text, reads, combined)
        if tuple_index is not None:
            raise Reject(f"{rule} takes params, not a tuple_index")
        p = dict(params or {})
        if spec is not None:  # PostUpdate
            if set(p) - POST_UPDATE_PARAMS:
                raise Reject(f"PostUpdate params are {sorted(POST_UPDATE_PARAMS)}")
            if text is None:
                raise Reject("PostUpdate needs text (the update body)")
            p["body"] = text
        elif text is not None:
            raise Reject(f"{rule} takes no free text")
        self._provenance(rule, p, combined.ids)
        return p, combined.read_seq, list(combined.view_keys)

    def _from_tuple(self, session, rule, spec, tuple_index, params, text, reads, combined):
        enabling = next((r for r in reads if r.rule == rule), None)
        if enabling is None:
            raise Reject(f"cite a list_actions read of {rule} to propose it")
        if tuple_index is None:
            raise Reject(f"{rule} is enumerated: give a tuple_index from the cited list_actions read")
        if tuple_index >= len(enabling.tuples):
            raise Reject(f"tuple_index {tuple_index} is out of range: the read lists {len(enabling.tuples)} tuples")
        extra = dict(params or {})
        if set(extra) - spec.knobs:
            raise Reject(f"{rule} takes a tuple_index and only {sorted(spec.knobs)} as params; got {sorted(extra)}")
        tuple_ = enabling.tuples[tuple_index]
        args = {a: tuple_[a] for a in spec.args}
        p = self._tuple_params(rule, tuple_, session.agent, args)
        if spec.text_field:
            if text is None:
                raise Reject(f"{rule} needs text ({spec.text_field})")
            p[spec.text_field] = text
        elif text is not None:
            raise Reject(f"{rule} takes no free text")
        self._apply_knobs(rule, p, extra)
        if "cites" in extra:
            self._provenance(rule, p, combined.ids)
        keys = set(enabling.tuple_keys[tuple_index])
        for read in reads:
            if read is not enabling:
                keys.update(read.keys)
        return p, combined.read_seq, sorted(keys)

    @staticmethod
    def _tuple_params(rule: str, t: Mapping, agent: str, args: Mapping) -> dict:
        if rule == "OpenIncident":
            incident = f"i-{t['alert']}"
            return {"alert": t["alert"], "incident": incident, "service": t["service"], "signature": t["signature"],
                    "tasks": [{"id": f"{incident}-t1", "kind": "investigate", "skill": "ops"},
                              {"id": f"{incident}-t2", "kind": "communicate", "skill": "triage"}]}
        if rule == "ClaimTask":
            return {"agent": agent, "task": t["task"], "ttl_ms": DEFAULT_TTL_MS}
        if rule == "CompleteTask":
            return {"task": t["task"]}
        if rule == "Handoff":
            return {"task": t["task"], "to": t["to"], "ttl_ms": DEFAULT_TTL_MS}
        if rule == "ResolveIncident":
            return {"incident": t["incident"]}
        if rule == "ProposeCause":
            return {"incident": t["incident"], "cause": t["cause"], "confidence": 50, "cites": []}
        if rule in ACTION_RULES:
            return {**args, **{k: v for k, v in t.items() if k != "index"}}
        return dict(args)

    @staticmethod
    def _apply_knobs(rule: str, p: dict, extra: Mapping) -> None:
        ttl = extra.get("ttl_ms")
        if "ttl_ms" in extra:
            if not _is_int(ttl) or not 0 < ttl <= MAX_TTL_MS:
                raise Reject(f"ttl_ms must be an integer in 1..{MAX_TTL_MS}")
            p["ttl_ms"] = ttl
        if "confidence" in extra:
            if not _is_int(extra["confidence"]) or not 0 <= extra["confidence"] <= 100:
                raise Reject("confidence must be an integer in 0..100")
            p["confidence"] = extra["confidence"]
        if "cites" in extra:
            p["cites"] = extra["cites"]

    @staticmethod
    def _provenance(rule: str, p: Mapping, shown: frozenset[str]) -> None:
        """Every entity id and fid a proposal names must have appeared in a cited read (§10.1 rule 4)."""
        named: list[str] = []
        if rule == "PostUpdate" or rule == "ProposeCause":
            if isinstance(p.get("incident"), str):
                named.append(p["incident"])
            cites = p.get("cites")
            if not isinstance(cites, list) or not all(isinstance(c, Mapping) for c in cites):
                raise Reject("cites must be a list of {s, fid} maps")
            for cite in cites:
                named.extend(v for v in (cite.get("s"), cite.get("fid")) if isinstance(v, str))
        else:
            named.extend(p[f] for f in REFERENCE_FIELDS if isinstance(p.get(f), str))
        missing = sorted({n for n in named if n not in shown})
        if missing:
            raise Reject(f"id not found in the cited reads: {', '.join(missing)}", missing=missing)


def _check_str(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise QueryError(f"{name} must be a non-empty string")


def _check_range(name: str, value: object, low: int, high: int) -> None:
    if not _is_int(value) or not low <= value <= high:
        raise QueryError(f"{name} must be an integer in {low}..{high}")


def dumps(value: object) -> str:
    """Deterministic JSON for tool results (contract C8)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
