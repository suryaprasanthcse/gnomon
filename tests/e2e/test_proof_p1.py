"""P1 · Races found before they run (master §26): the matrix and L1 refuse ClaimTask_v0; 50 claims show it and v1."""

import pytest

from gnomon.audit.runner import audit
from gnomon.footprint.lints import lint
from gnomon.footprint.matrix import build
from gnomon.kernel import macros
from gnomon.kernel.registry import Registry, RuleDef, register_rule_params
from gnomon.kernel.service import KernelHandle
from tests.support.ir_world import EPOCH
from tests.conftest import ROOT

pytestmark = [pytest.mark.e2e, pytest.mark.gate_g4b]

V0 = ROOT / "tests" / "fixtures" / "rules" / "claimtask_v0.cypher"
CLAIMANTS = ["inv-1", "tri-2"]


def claimtask_v0() -> RuleDef:
    return RuleDef(
        name="ClaimTask_v0", version=1, cls="memory", layer="core", caller=("investigator", "triage"), prelude="P0",
        body=macros.expand(V0.read_text(encoding="utf-8")),
        reads=("Agent", "Agent.id", "Task", "Task.id"), writes=("HOLDS", "Task.state"),
        key_templates=("HOLDS:*:{p.task}", "Task:{p.task}:state", "Task:*"),
    )


def test_matrix_and_l1_refuse_v0(opened):
    ir = opened
    v0 = claimtask_v0()
    report = lint(v0)
    l1 = next(r for r in report.results if r.id == "L1")
    assert not l1.ok and report.ok is False
    entries = {(e.a, e.b): e for e in build([*ir.k.registry.rules(), v0], ir.k.registry.fact_types())}
    assert entries[("ClaimTask_v0", "ClaimTask_v0")].cls == "unguarded"
    assert entries[("ClaimTask", "ClaimTask")].cls != "unguarded"
    refused = ir.k.submit("RegisterRule", register_rule_params(v0), pid="reg-v0", agent="ops-1")
    assert refused.code == "SCHEMA_REJECTED" and refused.detail["reason"] == "lint failed"


def claims(k, rule, params, n=50):
    outs = []
    for i in range(n):
        agent = CLAIMANTS[i % 2]
        outs.append(k.submit(rule, {**params, "agent": agent} if rule == "ClaimTask" else params,
                             pid=f"{rule}-{i:02d}", agent=agent, mode="replay", rec_pt=EPOCH + 100_000 + i * 10,
                             rec_lc=0))
    return outs


def test_v0_double_claims_and_k5a_fails_while_v1_has_one_winner(opened, db, redis_raw):
    ir = opened
    name_v0, name_v1 = ir.k.graph.name + ":p1v0", ir.k.graph.name + ":p1v1"
    redis_raw.execute_command("GRAPH.COPY", ir.k.graph.name, name_v0)
    redis_raw.execute_command("GRAPH.COPY", ir.k.graph.name, name_v1)
    v0 = claimtask_v0()
    k0 = KernelHandle(db.select_graph(name_v0), Registry([*ir.k.registry.rules(), v0], ir.k.registry.fact_types()))
    k0.graph.query("CREATE (:Rule {name: $n, sha: $s, version: 1, active: true, caller: $c})",
                   {"n": v0.name, "s": v0.sha, "c": list(v0.caller)})
    outs0 = claims(k0, "ClaimTask_v0", {"task": "t1", "ttl": 60_000})
    assert [o.code for o in outs0].count("APPLIED") == 50  # nothing stops them
    report0 = audit(db, redis_raw, name_v0, ["K5"])
    assert not report0.results["K5a"].ok
    assert report0.results["K5a"].findings[0]["task"] == "t1" and report0.results["K5a"].findings[0]["open_leases"] == 50

    k1 = KernelHandle(db.select_graph(name_v1), ir.k.registry)
    outs1 = claims(k1, "ClaimTask", {"task": "t1", "ttl_ms": 60_000})
    codes = [o.code for o in outs1]
    assert codes.count("APPLIED") == 1 and codes.count("REJECTED_GUARD") == 49
    assert audit(db, redis_raw, name_v1, ["K5"]).results["K5a"].ok
    for name in (name_v0, name_v1):
        redis_raw.execute_command("GRAPH.DELETE", name)
