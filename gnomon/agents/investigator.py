"""Investigator (master §13.1): claims investigation work, proposes the root cause, completes, resolves.

Order of preference: resolve a ready incident, propose the most recent deploy of the affected service as the
cause, complete the held task, claim open work. Investigators A and B run the same policy; which one works
depends only on who is alive and who holds the lease.
"""

from gnomon.agents.llm import Role

ROLE = Role("investigator", ("ResolveIncident", "ProposeCause", "CompleteTask", "ClaimTask"),
            "find the root cause of your incident, then complete your task; resolve when every guard holds")


def policy(context: dict) -> dict:
    rules = context["rules"]
    if rules.get("ResolveIncident", {}).get("tuples"):
        return {"rule": "ResolveIncident", "tuple_index": 0}
    for incident, per_rule in sorted(context.get("incidents", {}).items()):
        has_cause = any(f["rel"] == "ROOT_CAUSE" for f in per_rule.get("facts", []))
        if not has_cause and per_rule.get("ProposeCause", {}).get("tuples"):
            return {"rule": "ProposeCause", "incident": incident, "tuple_index": 0, "params": {"confidence": 80}}
    if rules.get("CompleteTask", {}).get("tuples"):
        return {"rule": "CompleteTask", "tuple_index": 0, "text": "root cause identified and recorded"}
    if rules.get("ClaimTask", {}).get("tuples"):
        return {"rule": "ClaimTask", "tuple_index": 0}
    return {"rule": None}
