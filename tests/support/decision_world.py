"""Decision-framework test world (A10): T-A activated by logged rewrites on the A06 incident world.

Activation is by M3 (RegisterRule) for RegisterScorer, RequestApproval, ApproveDecision and the two test action
rules, M6 (RegisterAgent) for the executive, and M5 (RegisterScorer) for a test scorer, so the log alone shows
when and by whom the layer was switched on (master §14, RUNBOOK_A A10).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support.ir_world import IR

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "tests" / "fixtures" / "rules"
SCORER = "Test.scorer"
SCORER_SHA = "c" * 64
EXECUTIVE = "exe-1"
HUMAN = "hum-1"


@dataclass
class DW:
    ir: IR
    marks: int = 0

    @property
    def k(self):
        return self.ir.k

    # ── envelope and commit
    def envelope(self, did="D-1", attempt=1, **over) -> dict:
        from gnomon.executive import envelope

        fid = self.ir.owned_by_fid()
        cites = [{"s": "svc:a", "fid": fid}]
        fields = {
            "did": did, "attempt": attempt, "aid": f"{did}/{attempt}", "kind": "action", "proposer": "agent:inv-1",
            "rationale_ref": "llm:9a0f", "scorer": SCORER, "scorer_sha": SCORER_SHA,
            "read_seq": self.k.head()["seq"], "hops": 4, "weight_scale": 2520, "lambda_n": 3, "lambda_d": 2,
            "tau": 2520, "margin": 1260, "baseline": {"impacted_digest": "d0", "teams": ["team:t1"], "region_size": 1},
            "candidates": [
                {"k": 0, "rule": "NoopAction", "params": {"mark": "m0"}, "params_sha": "p0", "outcome": "guard_pruned",
                 "reason": "G1 mark_new", "won": False},
                {"k": 1, "rule": "NoopAction", "params": {"mark": "m1"}, "params_sha": "p1", "outcome": "scored",
                 "benefit": 52920, "harm": 15120, "teams_new": 1, "score_num": 60480, "won": True}],
            "evidence": [{"ord": 0, "algo": SCORER, "role": "rescued", "path": ["svc:a"], "cites": cites}],
            "cites": cites, "footprint_digest": "f0", "workload_digest": None, "channels": {},
        }
        fields.update(over)
        return envelope.seal(fields)

    def params(self, d: dict, rule_params: dict | None = None) -> dict:
        from gnomon.executive import envelope

        return envelope.params_for(rule_params or {"mark": f"m{self.marks}"}, d)

    def commit(self, d: dict, *, rule="NoopAction", params=None, agent=EXECUTIVE, read_seq=None, attach=None,
               pid=None):
        from gnomon.executive import envelope

        p = params if params is not None else self.params(d)
        self.ir.clock += 10
        return self.k.submit(rule, p, pid=pid or envelope.pid_of(d["did"]), agent=agent,
                             read_seq=d["read_seq"] if read_seq is None else read_seq,
                             attach=envelope.attach_of(d) if attach is None else attach, mode="replay",
                             rec_pt=self.ir.clock, rec_lc=0)

    def request(self, d: dict, *, ttl=60_000, agent=EXECUTIVE, rule="NoopActionGated", pid=None, **over):
        from gnomon.executive import envelope

        p = {"aid": d["aid"], "did": d["did"], "decision_sha": d["sha"], "rule": rule,
             "params_sha": envelope.params_sha({"mark": f"m{self.marks}"}), "summary": "roll back the deploy",
             "incident": "i1", "ttl_ms": ttl}
        p.update(over)
        return self.ir.submit("RequestApproval", p, agent, pid=pid or f"req-{p['aid']}")

    def decide(self, aid: str, sha: str, verdict="approve", *, agent=HUMAN, at=None, **over):
        p = {"aid": aid, "decision_sha": sha, "verdict": verdict, "note": "seen"}
        p.update(over)
        return self.ir.submit("ApproveDecision", p, agent, pid=f"dec-{aid}-{verdict}", at=at)

    def approved(self, did="D-9", attempt=1, **over) -> dict:
        """A sealed envelope whose approval is requested and approved; returns the envelope."""
        d = self.envelope(did, attempt, **over)
        assert self.request(d).code == "APPLIED"
        assert self.decide(d["aid"], d["sha"]).code == "APPLIED"
        return d


def activate(ir: IR) -> DW:
    from gnomon.kernel.registry import layer_rules, load_rule_file, register_rule_params
    from gnomon.kernel.service import KernelService

    rules = [*layer_rules("TA"), load_rule_file(RULES / "noop_action.yaml"),
             load_rule_file(RULES / "noop_action_gated.yaml")]
    ir.k.registry = ir.k.registry.with_rules(*rules)
    ir.k.service = KernelService(ir.k.graph, ir.k.registry)
    for rule in rules:
        ir.ok("RegisterRule", register_rule_params(rule), "ops-1", pid=f"m3-{rule.name}")
    ir.ok("RegisterAgent", {"op": "register", "id": EXECUTIVE, "role": "executive", "skills": []}, "ops-1")
    ir.ok("RegisterScorer", {"name": SCORER, "sha": SCORER_SHA, "kind": "measured", "version": 1}, "ops-1")
    return DW(ir)


@pytest.fixture
def dw(opened) -> DW:
    return activate(opened)
