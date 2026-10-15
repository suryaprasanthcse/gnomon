"""Core tool implementations (master §20): plain functions of (query service, session) returning JSON-able values.

Only `propose` can write, and it does so through the kernel service behind the query service. Binding reads
return `{read_id, rows | tuples}` (see schemas.public_read); advisory tools return their results directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from gnomon.mcp.schemas import public_read
from gnomon.query.service import ACTION_RULES, QueryError, QueryService
from gnomon.query.session import Session


def candidate_view(c: Mapping) -> dict:
    """What an agent sees of a candidate: its rule, outcome and integer scores, never keys or shas of the kernel."""
    keep = ("k", "rule", "params", "outcome", "reason", "benefit", "harm", "teams_new", "score_num", "won")
    return {k: c.get(k) for k in keep}


@dataclass
class Tools:
    qs: QueryService
    session: Session
    executive: object | None = None

    def whoami(self) -> dict:
        info = self.qs.agent_info(self.session.agent)
        if info is None:
            raise QueryError(f"agent {self.session.agent!r} is not registered")
        return info

    def read_entity(self, uid: str) -> dict:
        return public_read(self.qs.read_entity(self.session, uid))

    def facts(self, uid: str) -> dict:
        return public_read(self.qs.facts(self.session, uid))

    def as_of(self, query: str, args: Mapping | None = None) -> dict:
        return public_read(self.qs.as_of(self.session, query, args))

    def incident_status(self, incident: str) -> dict:
        return public_read(self.qs.incident_status(self.session, incident))

    def list_actions(self, rule: str, args: Mapping | None = None) -> dict:
        return public_read(self.qs.list_actions(self.session, rule, args))

    def propose(self, rule: str, pid: str, tuple_index: int | None = None, params: Mapping | None = None,
                text: str | None = None, cite_reads: list[str] | None = None) -> dict:
        out = self.qs.propose(self.session, rule, pid=pid, tuple_index=tuple_index, params=params, text=text,
                              cite_reads=cite_reads or [])
        return {"outcome": out.code.value, "pid": pid, "seq": out.seq, "detail": out.detail}

    def neighborhood(self, svc: str, depth: int) -> dict:
        return self.qs.neighborhood(svc, depth)

    def rank_critical(self, k: int) -> list:
        return self.qs.rank_critical(k)

    def brokers(self, k: int, sampling: int) -> list:
        return self.qs.brokers(k, sampling)

    def failure_domains(self) -> list:
        return self.qs.failure_domains()

    def communities(self) -> list:
        return self.qs.communities()

    def dependency_path(self, source: str, target: str, max_len: int) -> dict | None:
        return self.qs.dependency_path(source, target, max_len)

    def why(self, uid: str | None = None, pid: str | None = None) -> dict | None:
        return self.qs.why(uid=uid, pid=pid)

    # ── T-B (master §20): present only when the server was given an Executive
    def _executive(self):
        info = self.qs.agent_info(self.session.agent)
        if self.executive is None or info is None or info["role"] != "responder":
            raise QueryError("only a responder may use the Executive")
        return self.executive

    def _nominations(self, nominations: list | None) -> list[dict]:
        resolved = []
        for n in nominations or []:
            try:
                read = self.session.get(n["read_id"])
            except (KeyError, TypeError) as exc:
                raise QueryError(f"unknown read in nomination: {n!r}") from exc
            index = n.get("tuple_index")
            if read.rule not in ACTION_RULES or not isinstance(index, int) or not 0 <= index < len(read.tuples):
                raise QueryError(f"a nomination names a tuple of a list_actions read of an action rule: {n!r}")
            resolved.append({"rule": read.rule, "params": {k: v for k, v in read.tuples[index].items() if k != "index"}})
        return resolved

    def simulate(self, incident: str, nominations: list | None = None) -> dict:
        res = self._executive().simulate(incident, self._nominations(nominations), request_id="simulate",
                                         proposer=f"agent:{self.session.agent}")
        return {"state": res.state, "did": res.did, "mode": res.mode, "error": res.error,
                "candidates": [candidate_view(c) for c in res.candidates]}

    def decide(self, incident: str, nominations: list | None, rationale: str, request_id: str) -> dict:
        res = self._executive().decide(incident, self._nominations(nominations), rationale, request_id,
                                       proposer=f"agent:{self.session.agent}")
        out = res.outcome
        return {"state": res.state, "did": res.did, "pid": res.pid, "attempt": res.attempt, "error": res.error,
                "outcome": out.code.value if out else None, "seq": out.seq if out else None,
                "candidates": [candidate_view(c) for c in res.candidates]}

    def decision(self, did: str) -> dict | None:
        from gnomon.executive.executive import read_decision

        self._executive()
        found = read_decision(self.qs.kernel.graph, did)
        if found is None:
            return None
        found["candidates"] = [candidate_view(c) for c in found["candidates"]]
        return found
