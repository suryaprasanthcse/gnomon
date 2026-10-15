"""Triage (master §13.1): opens an incident for an alert that nothing has triggered yet (R4)."""

from gnomon.agents.llm import Role

ROLE = Role("triage", ("OpenIncident",), "open an incident for an alert no incident covers yet")


def policy(context: dict) -> dict:
    if context["rules"].get("OpenIncident", {}).get("tuples"):
        return {"rule": "OpenIncident", "tuple_index": 0}
    return {"rule": None}
