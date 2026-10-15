"""§10.2 soundness: every tuple an enablement query returns, proposed at once with its view keys, is APPLIED.

Each example is a random walk over the enabled actions of one world (replay mode, explicit clock). At every step
the walk picks a rule and one of its current tuples with fresh reads; the proposal must be APPLIED. A rival write
between the read and the proposal must make it REJECTED_STALE instead (never applied, never a guard failure).
"""

import itertools
import uuid

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.conftest import tenant_scope
from tests.support.query_world import make_world, replay_clock

pytestmark = [pytest.mark.property, pytest.mark.gate_g4a]

ENUMERATED = {
    "OpenIncident": "triage-1", "ClaimTask": "inv-1", "CompleteTask": "inv-1", "Handoff": "inv-1",
    "ResolveIncident": "inv-1",
}
BY_INCIDENT = {"ProposeCause": "inv-1", "PostUpdate": "com-1"}
STEPS = st.lists(st.tuples(st.sampled_from(sorted([*ENUMERATED, *BY_INCIDENT, "Alert"])), st.integers(0, 5)),
                 min_size=1, max_size=22)
WORLD = settings(max_examples=12, deadline=None, suppress_health_check=list(HealthCheck))


def incidents(ir):
    return [r[0] for r in ir.k.read("MATCH (i:Incident) RETURN i.id ORDER BY i.id")]


def walk(ir, qs, steps):
    alerts, applied = itertools.count(1), 0
    for n, (rule, pick) in enumerate(steps):
        if rule == "Alert":
            service = "abc"[pick % 3]
            ir.alert(source_id=f"wal-{next(alerts)}", service=service, signature=f"sig{pick}")
            continue
        agent = ENUMERATED.get(rule) or BY_INCIDENT[rule]
        sess = qs.session(agent)
        if rule in ENUMERATED:
            read = qs.list_actions(sess, rule)
            if not read.tuples:
                continue
            index = pick % len(read.tuples)
            out = qs.propose(sess, rule, pid=f"w-{n}", tuple_index=index, cite_reads=[read.read_id],
                             text="done" if rule == "CompleteTask" else None)
        else:
            known = incidents(ir)
            if not known:
                continue
            incident = known[pick % len(known)]
            read = qs.list_actions(sess, rule, {"incident": incident})
            if not read.tuples:
                continue
            if rule == "ProposeCause":
                out = qs.propose(sess, rule, pid=f"w-{n}", tuple_index=pick % len(read.tuples),
                                 params={"confidence": 70}, cite_reads=[read.read_id])
            else:
                t = read.tuples[pick % len(read.tuples)]
                out = qs.propose(sess, rule, pid=f"w-{n}", cite_reads=[read.read_id], text="update",
                                 params={"incident": incident, "message_id": f"m-{n}",
                                         "cites": [{"s": t["s"], "fid": t["fid"]}]})
        assert out.code == "APPLIED", (n, rule, pick, out)
        applied += 1
    return applied


@given(steps=STEPS)
@WORLD
def test_enablement_soundness(db, redis_raw, golden_genesis, steps):
    from gnomon.query.service import QueryService

    with tenant_scope(redis_raw) as key:
        ir = make_world(db, redis_raw, golden_genesis, key)
        qs = QueryService(ir.k, mode="replay", clock=replay_clock(ir))
        for source_id, service, signature in (("seed-1", "a", "cpu"), ("seed-2", "b", "mem")):
            ir.alert(source_id=source_id, service=service, signature=signature)
        walk(ir, qs, steps)


@given(rule=st.sampled_from(["ClaimTask", "OpenIncident"]))
@settings(max_examples=2, deadline=None, suppress_health_check=list(HealthCheck))
def test_a_rival_write_makes_the_tuple_stale_not_applied(db, redis_raw, golden_genesis, rule):
    from gnomon.query.service import QueryService

    with tenant_scope(redis_raw) as key:
        ir = make_world(db, redis_raw, golden_genesis, key)
        qs = QueryService(ir.k, mode="replay", clock=replay_clock(ir))
        ir.alert(source_id="al-1", service="a", signature="cpu")
        ir.incident(incident="i0", alert="al-1", service="a", signature="cpu")
        ir.alert(source_id="al-2", service="b", signature="mem")
        agent = ENUMERATED[rule]
        sess = qs.session(agent)
        read = qs.list_actions(sess, rule)
        assert read.tuples
        if rule == "ClaimTask":
            ir.claim("t1", agent="rsp-1")  # the rival
        else:
            ir.incident(incident="i9", alert="al-2", service="b", signature="mem",
                        tasks=[{"id": "t9", "kind": "x", "skill": "ops"}])
        out = qs.propose(sess, rule, pid=f"rival-{uuid.uuid4().hex[:6]}", tuple_index=0, cite_reads=[read.read_id])
        assert out.code == "REJECTED_STALE", out
