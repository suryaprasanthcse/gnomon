"""§20 T-B rows: `decide`, `simulate`, `decision` over MCP; the Responder role; action rules stay out of `propose`."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from mcp import Client

from gnomon.agents import responder
from gnomon.agents.llm import AgentLoop
from gnomon.kernel.outcomes import Code, Outcome
from gnomon.mcp.server import create_server

pytestmark = [pytest.mark.integration, pytest.mark.gate_tb]

CORE_PLUS = {"decide", "simulate", "decision"}
ACTIONS = ("IsolateService", "FailoverService", "RollbackDeploy", "EscalateIncident", "RestoreService")


def run(server, name, args=None):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, args or {})

    result = asyncio.run(go())
    text = result.content[0].text if result.content else "null"
    return result.is_error, json.loads(text) if not result.is_error else text


@pytest.fixture
def server(xw):
    from gnomon.query.service import QueryService

    qs = QueryService(xw.k, mode="replay", clock=xw.ir_clock)
    return create_server(qs, agent="rsp-1", executive=xw.executive()), qs, xw


def test_t_b_tools_exist_only_with_an_executive(xw):
    from gnomon.query.service import QueryService

    qs = QueryService(xw.k)

    async def names(server):
        async with Client(server) as client:
            return {t.name for t in (await client.list_tools()).tools}

    assert not CORE_PLUS & asyncio.run(names(create_server(qs, agent="rsp-1")))
    assert CORE_PLUS <= asyncio.run(names(create_server(qs, agent="rsp-1", executive=xw.executive())))


def test_action_enablement_lists_tuples_but_actions_are_not_proposable(server):
    srv, qs, xw = server
    err, read = run(srv, "list_actions", {"rule": "FailoverService", "args": {"incident": "i1"}})
    assert not err
    assert [(t["svc"], t["replica"]) for t in read["tuples"]] == [("c", "c-r1"), ("c", "c-r2")]  # the unhealthy one too
    err, read = run(srv, "list_actions", {"rule": "IsolateService", "args": {"incident": "i1"}})
    assert [t["svc"] for t in read["tuples"]] == ["c"]
    sess = qs.session("rsp-1")
    cited = qs.list_actions(sess, "IsolateService", {"incident": "i1"})
    for rule in ACTIONS:
        out = qs.propose(sess, rule, pid=f"x-{rule}", tuple_index=0, cite_reads=[cited.read_id])
        assert out.code == "SCHEMA_REJECTED" and "not proposable" in out.detail["reason"], rule


def test_simulate_is_a_dry_run(server):
    srv, qs, xw = server
    err, read = run(srv, "list_actions", {"rule": "IsolateService", "args": {"incident": "i1"}})
    before = xw.k.head()
    err, out = run(srv, "simulate", {"incident": "i1", "nominations": [{"read_id": read["read_id"], "tuple_index": 0}]})
    assert not err and out["state"] == "SIMULATED"
    assert [c["rule"] for c in out["candidates"]][0] == "EscalateIncident"
    assert {"k", "rule", "outcome", "benefit", "harm", "score_num", "won"} <= set(out["candidates"][1])
    assert xw.k.head() == before and xw.sim_graphs() == []


def test_decide_commits_and_decision_reads_it_back(server):
    srv, qs, xw = server
    err, read = run(srv, "list_actions", {"rule": "FailoverService", "args": {"incident": "i1"}})
    err, out = run(srv, "decide", {"incident": "i1", "nominations": [{"read_id": read["read_id"], "tuple_index": 0}],
                                   "rationale": "the replica is healthy", "request_id": "q1"})
    assert not err and out["state"] == "DONE" and out["outcome"] == "APPLIED" and out["seq"] > 0
    assert not {"read_seq", "view_keys", "write_keys", "rule_sha"} & set(out)
    err, decision = run(srv, "decision", {"did": out["did"]})
    assert not err and decision["did"] == out["did"] and decision["scorer"].endswith(".blastRadius")
    assert [c["rule"] for c in decision["candidates"]][0] == "EscalateIncident"
    assert decision["committed_as"] == out["pid"] and decision["evidence"]
    again = run(srv, "decide", {"incident": "i1", "nominations": [], "rationale": "again", "request_id": "q1"})[1]
    assert again["outcome"] == "DUPLICATE"  # the same request id after a commit


def test_nominations_must_come_from_reads_of_this_session(server):
    srv, qs, xw = server
    err, _ = run(srv, "decide", {"incident": "i1", "nominations": [{"read_id": "rd-999", "tuple_index": 0}],
                                 "rationale": "r", "request_id": "q2"})
    assert err is True
    assert xw.k.read("MATCH (x:Decision) RETURN count(x)") == [[0]]


def test_only_a_responder_may_decide(xw):
    from gnomon.query.service import QueryService

    qs = QueryService(xw.k)
    srv = create_server(qs, agent="inv-1", executive=xw.executive())
    err, _ = run(srv, "decide", {"incident": "i1", "nominations": [], "rationale": "r", "request_id": "q"})
    assert err is True
    assert run(srv, "simulate", {"incident": "i1", "nominations": []})[0] is True


# ── the Responder role ─────────────────────────────────────────────────────────────────────────────────────────
def context(**per_incident):
    return {"role": "responder", "agent": "rsp-1", "step": 1, "memory": {"done": []}, "rules": {}, "held": [],
            "incidents": {"i1": {k: {"tuples": v} for k, v in per_incident.items()}}}


def test_the_responder_nominates_every_enabled_action_best_first():
    ctx = context(IsolateService=[{"index": 0, "incident": "i1", "svc": "c"}],
                  FailoverService=[{"index": 0, "incident": "i1", "svc": "c", "replica": "c-r1"}],
                  RollbackDeploy=[{"index": 0, "incident": "i1", "svc": "c", "bad": "x", "prev": "y"}])
    decision = responder.policy(ctx)
    assert decision["rule"] == "decide" and decision["incident"] == "i1"
    assert [n["rule"] for n in decision["nominations"]] == ["RollbackDeploy", "FailoverService", "IsolateService"]
    assert decision["rationale"]


def test_the_responder_decides_once_per_incident():
    ctx = context(IsolateService=[{"index": 0, "incident": "i1", "svc": "c"}])
    ctx["memory"]["done"] = ["decide:i1"]
    assert responder.policy(ctx) == {"rule": None}
    assert responder.policy(context()) == {"rule": None}  # nothing enabled


def test_the_agent_loop_hands_a_decide_to_the_executive():
    calls = []

    class Exec:
        def decide(self, incident, nominations, rationale, request_id, proposer=None):
            calls.append((incident, nominations, rationale, request_id, proposer))
            return SimpleNamespace(state="DONE", outcome=Outcome(Code.APPLIED, seq=9), did="D-1")

    tuples = [{"index": 0, "incident": "i1", "svc": "c"}]

    class QS:
        def session(self, agent):
            return SimpleNamespace(agent=agent)

        def list_actions(self, sess, rule, args=None):
            rows = {"CompleteTask": [{"index": 0, "task": "t1"}], "IsolateService": tuples}.get(rule, [])
            return SimpleNamespace(read_id=f"rd-{rule}", tuples=rows)

        def read_entity(self, sess, uid):
            return SimpleNamespace(read_id="rd-e", rows=[{"labels": ["Task"], "props": {"incident": "i1"}}])

        def facts(self, sess, uid):
            return SimpleNamespace(read_id="rd-f", rows=[])

    class LLM:
        def complete(self, prompt):
            return json.dumps(responder.policy(json.loads(prompt)))

    loop = AgentLoop("rsp-1", responder.ROLE, QS(), LLM(), executive=Exec())
    res = loop.step()
    assert res.state == "applied" and res.rule == "decide"
    ((incident, nominations, rationale, request_id, proposer),) = calls
    assert incident == "i1" and nominations == [{"rule": "IsolateService", "params": {"incident": "i1", "svc": "c"}}]
    assert request_id == res.pid and proposer == "agent:rsp-1" and rationale
    assert loop.done == ["decide:i1"]
