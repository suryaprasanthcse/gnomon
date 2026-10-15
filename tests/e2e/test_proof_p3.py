"""P3 · Crash and exactly-once (master §26): agents are data, so a crash loses and repeats nothing."""

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.gate_g4b]


def test_a_killed_agent_loses_nothing(core_run):
    r, k = core_run, core_run.kernel
    crash = r.crash
    assert crash["agent"] == "inv-a" and crash["rule"] == "ClaimTask"
    (lease,) = k.read("MATCH (a:Agent {id: 'inv-a'})-[l:HOLDS]->(t:Task {id: $t}) RETURN l.close_reason, l.closed_seq, "
                      "l.from_seq", {"t": crash["task"]})
    assert lease[0] == "expired"  # R8 closed the dead agent's lease
    assert k.read("MATCH (r:Rewrite {id: $p}) RETURN r.rule", {"p": f"exp:{crash['task']}:{lease[2]}"}) == \
        [["ExpireLease"]]
    assert k.read("MATCH (a:Agent {id: 'inv-b'})-[l:HOLDS]->(:Task {id: $t}) RETURN l.close_reason",
                  {"t": crash["task"]}) == [["completed"]]  # another agent continued and finished it
    assert k.read("MATCH (t:Task {id: $t}) RETURN t.state", {"t": crash["task"]}) == [["done"]]


def test_the_replayed_proposal_is_a_duplicate(core_run):
    crash = core_run.crash
    assert crash["replay"].code == "DUPLICATE"
    assert core_run.kernel.read("MATCH (r:Rewrite {id: $p}) RETURN count(r)", {"p": crash["pid"]}) == [[1]]


def test_one_delivery_per_outbox_pid(core_run):
    k = core_run.kernel
    pids = [r[0] for r in k.read("MATCH (o:Outbox) RETURN o.pid ORDER BY o.pid")]
    assert pids and k.read("MATCH (o:Outbox) WHERE o.state <> 'delivered' RETURN count(o)") == [[0]]
    for pid in pids:
        assert core_run.infra.apply_count(pid) == 1
        assert k.read("MATCH (e:Event {kind: 'receipt'}) WHERE e.source_id STARTS WITH $p RETURN count(e)",
                      {"p": f"rcpt:{pid}:"}) == [[1]]
