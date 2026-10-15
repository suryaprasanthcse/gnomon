"""Core tool implementations (master §20): plain functions of (query service, session) returning JSON-able values.

Only `propose` can write, and it does so through the kernel service behind the query service. Binding reads
return `{read_id, rows | tuples}` (see schemas.public_read); advisory tools return their results directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from gnomon.mcp.schemas import public_read
from gnomon.query.service import QueryError, QueryService
from gnomon.query.session import Session


@dataclass
class Tools:
    qs: QueryService
    session: Session

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
