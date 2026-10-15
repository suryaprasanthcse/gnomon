"""Tool-facing shapes (master §10.3, §20): what an agent may send and what it may see.

An agent never sees or supplies `read_seq`, `view_keys`, `write_keys`, `rule_sha` or `$agent` (§10.3 rule 6), so
binding reads reach it as `{read_id, rows | tuples}` only, and the propose tool has no field for any of them.
"""

from __future__ import annotations

from gnomon.query.service import TEXT_MAX
from gnomon.query.session import Read

AS_OF_QUERIES = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6")
PROPOSE_FIELDS = ("rule", "tuple_index", "params", "text", "cite_reads", "pid")
CORE_TOOLS = ("whoami", "read_entity", "facts", "as_of", "incident_status", "list_actions", "propose", "neighborhood",
              "rank_critical", "brokers", "failure_domains", "communities", "dependency_path", "why")

__all__ = ["AS_OF_QUERIES", "CORE_TOOLS", "PROPOSE_FIELDS", "TEXT_MAX", "public_read"]


def public_read(read: Read) -> dict:
    """What an agent sees of a binding read: its id and its content, never the snapshot seq or the keys."""
    if read.rule is not None:
        return {"read_id": read.read_id, "tuples": [dict(t) for t in read.tuples]}
    return {"read_id": read.read_id, "rows": [dict(r) for r in read.rows]}
