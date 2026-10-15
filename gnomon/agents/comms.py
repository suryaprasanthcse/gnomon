"""Comms (master §13.1): tells the status page what the log knows, citing live facts, then completes its task."""

from gnomon.agents.llm import Role

ROLE = Role("comms", ("PostUpdate", "CompleteTask", "ClaimTask"),
            "post one status update per incident once a root cause is recorded, citing it, then complete your task")


def policy(context: dict) -> dict:
    done = set(context["memory"]["done"])
    for incident, per_rule in sorted(context.get("incidents", {}).items()):
        if f"PostUpdate:{incident}" in done:
            continue
        cause = next((t for t in per_rule.get("PostUpdate", {}).get("tuples", []) if t["rel"] == "ROOT_CAUSE"), None)
        if cause:
            return {"rule": "PostUpdate", "incident": incident, "text": f"Incident {incident}: root cause found.",
                    "params": {"incident": incident, "message_id": f"m-{incident}",
                               "cites": [{"s": cause["s"], "fid": cause["fid"]}]}}
    for held in context.get("held", []):
        if f"PostUpdate:{held['incident']}" in done:
            for tuple_ in context["rules"].get("CompleteTask", {}).get("tuples", []):
                if tuple_["task"] == held["task"]:
                    return {"rule": "CompleteTask", "tuple_index": tuple_["index"], "text": "status update posted"}
    if context["rules"].get("ClaimTask", {}).get("tuples"):
        return {"rule": "ClaimTask", "tuple_index": 0}
    return {"rule": None}
