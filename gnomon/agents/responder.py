"""Responder (master §13.1, §15.1): claims work like any agent, and nominates actions for the Executive through `decide`.

The Responder never proposes an action rule itself. It reads which actions are enabled for an incident it holds a task
of, nominates every one of them best-first (the Executive simulates them all when there are few, §15.3), and says why.
"""

from gnomon.agents.llm import Role

ACTIONS = ("RollbackDeploy", "FailoverService", "IsolateService", "RestoreService")  # nomination order, best first
ROLE = Role("responder", ("CompleteTask", "ClaimTask", *ACTIONS, "EscalateIncident"),
            "nominate the actions that could end the incident; the Executive simulates and commits the best one",
            tools=("decide",))


def policy(context: dict) -> dict:
    done = set(context["memory"]["done"])
    for incident, per_rule in sorted(context.get("incidents", {}).items()):
        if f"decide:{incident}" in done:
            continue
        nominations = [{"rule": rule, "tuple_index": t["index"]} for rule in ACTIONS
                       for t in per_rule.get(rule, {}).get("tuples", [])]
        if nominations:
            return {"rule": "decide", "incident": incident, "nominations": nominations,
                    "rationale": f"{len(nominations)} enabled action(s) for {incident}; simulate and take the best"}
    if context["rules"].get("ClaimTask", {}).get("tuples"):
        return {"rule": "ClaimTask", "tuple_index": 0}
    return {"rule": None}
