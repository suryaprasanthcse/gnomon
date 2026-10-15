"""The MCP server for one agent session (master §20, Core rows; platform fact S12).

`create_server(qs, agent)` binds the server to one agent: `$agent` is the session's, never a tool argument.
Tool results are JSON text, so every tool answers with one content block whatever its shape.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from gnomon.mcp.tools import Tools
from gnomon.query.service import QueryError, QueryService, dumps


def create_server(qs: QueryService, agent: str) -> MCPServer:
    if qs.agent_info(agent) is None:
        raise QueryError(f"agent {agent!r} is not registered")
    tools = Tools(qs, qs.session(agent))
    server = MCPServer("gnomon")

    @server.tool()
    def whoami() -> str:
        """The calling agent's id, role and skills, as registered."""
        return dumps(tools.whoami())

    @server.tool()
    def read_entity(uid: str) -> str:
        """Read one entity's labels and properties by uid (for example svc:a). Binding read: returns a read_id to cite."""
        return dumps(tools.read_entity(uid))

    @server.tool()
    def facts(uid: str) -> str:
        """Live facts touching an entity (relationship, direction, other end, fid). Binding read: returns a read_id."""
        return dumps(tools.facts(uid))

    @server.tool()
    def as_of(query: Literal["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"], args: dict[str, Any]) -> str:
        """Bitemporal ownership queries. Q1 owner at valid time T as known after rewrite S (svc, T, S); Q2 current
        history of a service (svc); Q3 belief after rewrite S (svc, S); Q4 services a team owned at T (team, T);
        Q5 ownership changes in a window (T1, T2); Q6 owners when an incident opened (incident). Binding read."""
        return dumps(tools.as_of(query, args))

    @server.tool()
    def incident_status(incident: str) -> str:
        """An incident's state with its tasks, their states and live lease holders. Binding read."""
        return dumps(tools.incident_status(incident))

    @server.tool()
    def list_actions(rule: str, args: dict[str, Any] | None = None) -> str:
        """The valid next actions for a rule as indexed tuples (choose one with propose and its tuple_index).
        Rules: OpenIncident, ClaimTask, CompleteTask, Handoff, ResolveIncident, and, with args {incident},
        PostUpdate (citeable facts) and ProposeCause (candidate deploys). Binding read."""
        return dumps(tools.list_actions(rule, args))

    @server.tool()
    def propose(rule: str, pid: str, tuple_index: int | None = None, params: dict[str, Any] | None = None,
                text: str | None = None, cite_reads: list[str] | None = None) -> str:
        """Propose one rule application. Enumerated rules take tuple_index from a cited list_actions read; rules with
        open parameters (PostUpdate, AssertFact.*, RetractFact.*) take params whose ids must appear in cited reads.
        text carries the free text (update body, task result). pid is your unique proposal id."""
        return dumps(tools.propose(rule, pid, tuple_index, params, text, cite_reads))

    @server.tool()
    def neighborhood(svc: str, depth: int) -> str:
        """Services downstream and upstream of a service within depth hops over current dependencies. Advisory."""
        return dumps(tools.neighborhood(svc, depth))

    @server.tool()
    def rank_critical(k: int) -> str:
        """The k services most depended upon (PageRank over current dependencies). Advisory."""
        return dumps(tools.rank_critical(k))

    @server.tool()
    def brokers(k: int, sampling: int) -> str:
        """The k services that sit on the most dependency paths (betweenness, fixed seed). Advisory."""
        return dumps(tools.brokers(k, sampling))

    @server.tool()
    def failure_domains() -> str:
        """Weakly connected groups of services over current dependencies. Advisory."""
        return dumps(tools.failure_domains())

    @server.tool()
    def communities() -> str:
        """Dependency communities (label propagation). Advisory."""
        return dumps(tools.communities())

    @server.tool()
    def dependency_path(source: str, target: str, max_len: int) -> str:
        """The shortest dependency path between two services, in either direction, or null. Advisory."""
        return dumps(tools.dependency_path(source, target, max_len))

    @server.tool()
    def why(uid: str | None = None, pid: str | None = None) -> str:
        """Provenance: the rewrite that last wrote an entity (uid) or one proposal's rewrite (pid), with its causes."""
        return dumps(tools.why(uid, pid))

    return server
