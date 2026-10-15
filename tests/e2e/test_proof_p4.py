"""P4 · Why, and time travel (master §26): every change explains itself, at any point in time."""

import pytest

from gnomon.query.service import QueryService

pytestmark = [pytest.mark.e2e, pytest.mark.gate_g4b]

INF = 9007199254740991


def agent_of(k, seq):
    return k.read("MATCH (r:Rewrite {seq: $s}) RETURN r.agent", {"s": seq})[0][0]


def test_why_chain_crosses_four_agents(core_run):
    k = core_run.kernel
    qs = QueryService(k)
    (uid,) = [r[0] for r in k.read("MATCH (m:Message) RETURN m.uid ORDER BY m.uid LIMIT 1")]
    why = qs.why(uid=uid)
    assert why["rule"] == "PostUpdate" and why["agent"] == "com-1" and why["causes"]
    agents = {why["agent"]} | {agent_of(k, seq) for chain in why["causes"] for seq in chain}
    assert {"com-1", "triage-1", "ing-1"} <= agents and agents & {"inv-a", "inv-b"}


def test_time_travel_on_a_service_with_a_backdated_ownership_change(core_run):
    k = core_run.kernel
    qs = QueryService(k)
    sess = qs.session("inv-a")
    svc, (old, new) = next((s, v[:2]) for s, v in sorted(core_run.load.ownership.items()) if len(v) >= 2)
    (old_team, vf0, seq0), (new_team, vf1, seq1) = old, new
    assert seq1 > seq0 and vf0 < vf1

    def q1(T, S):
        return [r["team"] for r in qs.as_of(sess, "Q1", {"svc": svc, "T": T, "S": S}).rows]

    head = k.head()["seq"]
    assert q1(vf1 + 1, seq0) == [old_team]  # what we believed before the change: the old owner, still
    assert q1(vf1 + 1, head) == [new_team]  # what we believe now about the same instant
    assert q1(vf0 + 1, head) == [old_team]  # and the earlier instant keeps its owner
    history = [(r["team"], r["valid_from"], r["valid_to"]) for r in qs.as_of(sess, "Q2", {"svc": svc}).rows]
    assert history[0] == (old_team, vf0, vf1) and history[-1][0] == new_team
    believed = [(r["team"], r["valid_to"]) for r in qs.as_of(sess, "Q3", {"svc": svc, "S": seq0}).rows]
    assert believed == [(old_team, INF)]
