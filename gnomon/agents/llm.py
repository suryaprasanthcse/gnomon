"""The LLM agent loop (master §13.2): perceive, enable, choose, propose, handle the outcome.

The model sees one canonical JSON prompt (what the agent read through the tools) and answers with one JSON
decision: a rule, a `tuple_index` or `params`, and free `text`. The loop cites the reads it used, so ids and
view keys come from the query service and never from the model (§10.3). `$agent` is the session's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

AGENT_STALE_RETRIES = 3  # Appendix C
INCIDENT_RULES = ("ProposeCause", "PostUpdate")  # enablement reads scoped to one incident


class AgentBug(RuntimeError):
    """CALLER_DENIED: the role asked for something it may not do. Stop and surface it (§13.2)."""


@dataclass(frozen=True)
class Role:
    name: str
    rules: tuple[str, ...]
    instruction: str = ""


@dataclass
class StepResult:
    state: str  # applied | idle | reenable | gave_up_stale | surfaced
    attempts: int = 0
    rule: str | None = None
    pid: str | None = None
    outcome: object | None = None
    tuple: dict | None = None


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class AgentLoop:
    def __init__(self, agent: str, role: Role, qs, llm) -> None:
        self.agent, self.role, self.qs, self.llm = agent, role, qs, llm
        self.session = qs.session(agent)
        self.steps = 0
        self.done: list[str] = []
        self.surfaced: list[dict] = []
        self.last_request: dict | None = None

    # ── perceive and enable
    def _perceive(self) -> tuple[dict, dict]:
        qs, sess = self.qs, self.session
        rules: dict = {}
        reads: dict = {}
        for rule in self.role.rules:
            if rule not in INCIDENT_RULES:
                read = qs.list_actions(sess, rule)
                rules[rule] = {"tuples": list(read.tuples)}
                reads[(rule, None)] = read
        held = []
        for tuple_ in rules.get("CompleteTask", {}).get("tuples", []):
            entity = qs.read_entity(sess, f"task:{tuple_['task']}")
            if entity.rows:
                held.append({"task": tuple_["task"], "incident": entity.rows[0]["props"].get("incident")})
        incidents: dict = {}
        wanted = [r for r in self.role.rules if r in INCIDENT_RULES]
        for incident in sorted({h["incident"] for h in held if h["incident"]}) if wanted else []:
            seen = qs.facts(sess, f"inc:{incident}")  # what the log already holds about the incident
            incidents.setdefault(incident, {})["facts"] = [{"rel": r["rel"], "other": r["other"]} for r in seen.rows]
            for rule in wanted:
                read = qs.list_actions(sess, rule, {"incident": incident})
                incidents.setdefault(incident, {})[rule] = {"tuples": list(read.tuples)}
                reads[(rule, incident)] = read
        context = {"role": self.role.name, "agent": self.agent, "instruction": self.role.instruction,
                   "step": self.steps, "memory": {"done": sorted(self.done)}, "rules": rules, "held": held,
                   "incidents": incidents}
        return context, reads

    # ── one step
    def step(self) -> StepResult:
        self.steps += 1
        pid = f"{self.agent}:{self.steps}"
        attempts = 0
        while True:
            context, reads = self._perceive()
            decision = json.loads(self.llm.complete(canonical(context)))
            rule = decision.get("rule")
            if rule is None:
                return StepResult("idle", attempts, None, pid)
            incident = decision.get("incident")
            read = reads.get((rule, incident if rule in INCIDENT_RULES else None))
            if rule not in self.role.rules or read is None:
                self.surfaced.append({"pid": pid, "rule": rule, "error": "the model chose a rule outside its role"})
                return StepResult("idle", attempts, rule, pid)
            request = {"rule": rule, "pid": pid, "tuple_index": decision.get("tuple_index"),
                       "params": decision.get("params"), "text": decision.get("text"), "cite_reads": [read.read_id]}
            chosen = None
            if request["tuple_index"] is not None and request["tuple_index"] < len(read.tuples):
                chosen = dict(read.tuples[request["tuple_index"]])
            attempts += 1
            self.last_request = request
            out = self.qs.propose(self.session, **request)
            code = out.code.value if hasattr(out.code, "value") else out.code
            if code in ("APPLIED", "DUPLICATE"):
                about = (decision.get("params") or {}).get("incident") or (chosen or {}).get("incident")
                if about:
                    self.done.append(f"{rule}:{about}")
                return StepResult("applied", attempts, rule, pid, out, chosen)
            if code == "REJECTED_STALE":
                if attempts > AGENT_STALE_RETRIES:
                    return StepResult("gave_up_stale", attempts, rule, pid, out, chosen)
                continue  # re-read what changed (a fresh perceive) and choose again
            if code == "CALLER_DENIED":
                raise AgentBug(f"{self.agent} was denied {rule}: {out.detail}")
            if code == "KERNEL_ERROR":
                self.surfaced.append({"pid": pid, "rule": rule, "error": out.detail.get("error")})
                return StepResult("surfaced", attempts, rule, pid, out, chosen)
            return StepResult("reenable", attempts, rule, pid, out, chosen)  # guard or schema: enable again next step
