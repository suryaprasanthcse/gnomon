"""I6 (master §0.4): at most one unclosed HOLDS lease per task, under random lifecycle sequences.

Each example copies a base tenant holding an open incident with two tasks, then applies a random sequence
of claims, completions, handoffs, expiries and clock jumps (replay mode, explicit pt). Proposals may be
rejected; after every step no task may have two unclosed leases, and a task with an unclosed live lease is
`claimed`.
"""

import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

pytestmark = [pytest.mark.property, pytest.mark.gate_g2b]

AGENTS = ["inv-1", "rsp-1", "tri-2", "com-1"]
TASKS = ["t1", "t2"]
OP = st.one_of(
    st.tuples(st.just("claim"), st.sampled_from(AGENTS), st.sampled_from(TASKS), st.sampled_from([50, 500, 5_000])),
    st.tuples(st.just("complete"), st.sampled_from(AGENTS), st.sampled_from(TASKS)),
    st.tuples(st.just("handoff"), st.sampled_from(AGENTS), st.sampled_from(TASKS), st.sampled_from(AGENTS)),
    st.tuples(st.just("expire"), st.sampled_from(TASKS)),
    st.tuples(st.just("wait"), st.sampled_from([10, 100, 1_000, 10_000])),
)


@pytest.fixture(scope="module")
def lifecycle_base(db, redis_raw, golden_genesis, genesis_config):
    from tests.support.ir_world import AGENTS as WORLD_AGENTS
    from tests.support.ir_world import IR

    from gnomon.kernel.service import KernelHandle

    key = f"t:test-life-{uuid.uuid4().hex[:8]}"
    redis_raw.execute_command("GRAPH.COPY", golden_genesis, key)
    w = IR(KernelHandle(db.select_graph(key), genesis_config["registry"]), redis_raw)
    for aid, role, skills in WORLD_AGENTS:
        w.ok("RegisterAgent", {"op": "register", "id": aid, "role": role, "skills": skills}, "ops-1")
    w.ok("RegisterEntity.Service", {"id": "a", "name": "a", "tier": "t", "criticality": 3, "user_facing": True},
         "ops-1")
    w.alert()
    w.incident(tasks=[{"id": "t1", "kind": "k", "skill": "ops"}, {"id": "t2", "kind": "k", "skill": "triage"}])
    yield key, w.clock
    redis_raw.execute_command("GRAPH.DELETE", key)


def violations(graph, now):
    double = graph.query("MATCH (:Agent)-[l:HOLDS]->(t:Task) WHERE l.closed_seq IS NULL "
                         "WITH t, count(l) AS n WHERE n > 1 RETURN t.id").result_set
    live_but_open = graph.query("MATCH (:Agent)-[l:HOLDS]->(t:Task) WHERE l.closed_seq IS NULL AND l.until_pt > $now "
                                "AND t.state <> 'claimed' RETURN t.id", {"now": now}).result_set
    return double, live_but_open


@settings(max_examples=40, deadline=None)
@given(ops=st.lists(OP, min_size=1, max_size=16))
def test_random_lifecycles_keep_i6(db, redis_raw, lifecycle_base, genesis_config, ops):
    from tests.support.ir_world import IR

    from gnomon.kernel.service import KernelHandle

    base, clock = lifecycle_base
    key = f"{base}:ex{uuid.uuid4().hex[:8]}"
    redis_raw.execute_command("GRAPH.COPY", base, key)
    w = IR(KernelHandle(db.select_graph(key), genesis_config["registry"]), redis_raw, clock=clock)
    try:
        for op in ops:
            if op[0] == "claim":
                _, agent, task, ttl = op
                w.submit("ClaimTask", {"agent": agent, "task": task, "ttl_ms": ttl}, agent)
            elif op[0] == "complete":
                _, agent, task = op
                w.submit("CompleteTask", {"task": task, "result": "r"}, agent)
            elif op[0] == "handoff":
                _, agent, task, to = op
                w.submit("Handoff", {"task": task, "to": to, "ttl_ms": 500}, agent)
            elif op[0] == "expire":
                w.submit("ExpireLease", {"task": op[1]}, "sch-1")
            else:
                w.clock += op[1]
            assert violations(w.k.graph, w.clock) == ([], []), ops
    finally:
        redis_raw.execute_command("GRAPH.DELETE", key)
