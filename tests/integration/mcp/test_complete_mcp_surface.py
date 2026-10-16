"""§20 in full: every catalogue tool is registered with a schema, the T-F `asof` arguments reach the scratch graphs, the
tools that need a layer exist only with it, and the human-only commands are not on the surface at all.

The server under test is the federated Executive world of A14 (T-A to T-F, scorer v2) with every optional part attached.
"""

import asyncio
import json

import pytest
from mcp import Client

from gnomon.mcp.server import create_server
from gnomon.query.service import QueryService
from gnomon.temporal.asof import AsofGraphs
from tests.support.fed_blast_world import fxw  # noqa: F401
from tests.support.ir_world import ir, opened  # noqa: F401
from tests.support.query_world import qs  # noqa: F401, E402

pytestmark = [pytest.mark.integration, pytest.mark.gate_te]

CATALOGUE = {"whoami", "read_entity", "facts", "as_of", "incident_status", "list_actions", "propose", "neighborhood",
             "rank_critical", "brokers", "failure_domains", "communities", "dependency_path", "why", "simulate", "decide",
             "decision", "optimizer_status", "send_notice", "fedlog"}
ADVISORY = {"neighborhood", "rank_critical", "brokers", "failure_domains", "communities", "dependency_path"}
HUMAN_ONLY = ("approve", "veto", "register_rule", "deactivate_rule", "register_scorer", "register_agent",
              "register_entity", "audit", "replay", "counterfactual", "quarantine")
HUMAN_RULES = ("ApproveDecision", "RegisterRule", "DeactivateRule", "RegisterScorer", "RegisterAgent",
               "RegisterEntity.Service", "RegisterEntity.Team", "OpenChannel", "IndexChange", "IndexSettled")


def call(server, name, args=None):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, args or {})

    result = asyncio.run(go())
    text = result.content[0].text if result.content else "null"
    try:
        return result.is_error, json.loads(text)
    except ValueError:
        return result.is_error, text


def listed(server):
    async def go():
        async with Client(server) as client:
            return await client.list_tools()

    return {t.name: t for t in asyncio.run(go()).tools}


class World:
    def __init__(self, fxw, db, redis_raw) -> None:
        self.xw, self.db, self.redis = fxw, db, redis_raw
        self.graph = fxw.k.graph
        self.wall = lambda: 1_000

    def server(self, agent="rsp-1", executive=True, **kw):
        qs = QueryService(self.xw.k, mode="replay", clock=self.xw.ir_clock)
        return create_server(qs, agent, executive=self.xw.executive(federation=True) if executive else None,
                             asof=AsofGraphs(self.db, self.redis, self.graph.name, self.wall), optimizer=True,
                             federation={"prov": self.graph}, **kw)


@pytest.fixture
def world(fxw, db, redis_raw):  # noqa: F811
    return World(fxw, db, redis_raw)


# ── the catalogue

def test_the_full_catalogue_is_registered_with_a_description_and_an_object_schema(world):
    tools = listed(world.server())
    assert set(tools) == CATALOGUE
    for name, tool in tools.items():
        assert tool.description, name
        assert tool.input_schema["type"] == "object", name


def test_the_layer_tools_are_absent_without_their_layer(opened, qs):  # noqa: F811
    assert set(listed(create_server(qs, "inv-1"))) == CATALOGUE - {"simulate", "decide", "decision", "optimizer_status",
                                                                  "send_notice", "fedlog"}


def test_optimizer_status_and_fedlog_are_opt_in_and_send_notice_needs_the_rule(world):
    plain = QueryService(world.xw.k, mode="replay", clock=world.xw.ir_clock)
    assert "optimizer_status" not in listed(create_server(plain, "rsp-1"))
    assert "fedlog" not in listed(create_server(plain, "rsp-1", optimizer=True))
    assert "send_notice" in listed(create_server(plain, "com-1"))  # T-D is active in this world


def test_the_input_schemas_match_the_catalogue(world):
    props = {n: set(t.input_schema.get("properties", {})) for n, t in listed(world.server()).items()}
    asof = {"asof", "asof_seq"}
    assert props["optimizer_status"] == set()
    assert props["send_notice"] == {"peer", "incident", "body", "pid"}
    assert props["fedlog"] == {"from_pt", "to_pt"}
    assert props["simulate"] == {"incident", "nominations"}
    assert props["decide"] == {"incident", "nominations", "rationale", "request_id"}
    assert props["decision"] == {"did"}
    for name in ADVISORY:
        assert asof <= props[name], name
    required = {n: set(t.input_schema.get("required", [])) for n, t in listed(world.server()).items()}
    assert required["send_notice"] == {"peer", "incident", "body", "pid"} and required["fedlog"] == set()
    assert all(not (asof & required[n]) for n in ADVISORY)  # asof arguments are optional


# ── strict separation

def test_no_human_only_command_is_a_tool(world):
    names = set(listed(world.server()))
    for word in HUMAN_ONLY:
        assert not [n for n in names if word in n.replace("-", "_")], word


@pytest.mark.parametrize("rule", HUMAN_RULES)
def test_propose_refuses_the_rules_only_humans_and_operators_may_apply(world, rule):
    err, out = call(world.server(), "propose", {"rule": rule, "pid": f"hx-{rule}", "params": {}})
    assert not err and out["outcome"] == "SCHEMA_REJECTED", out


def test_no_tool_accepts_kernel_internals(world):
    forbidden = {"read_seq", "view_keys", "write_keys", "rule_sha", "agent", "$agent", "cypher", "sha"}
    for name, tool in listed(world.server()).items():
        assert not forbidden & set(tool.input_schema.get("properties", {})), name


# ── basic invocation of every tool

def test_every_read_tool_answers_a_valid_call(world):
    server = world.server()
    calls = {
        "whoami": {}, "read_entity": {"uid": "svc:c"}, "facts": {"uid": "svc:c"},
        "as_of": {"query": "Q2", "args": {"svc": "c"}}, "incident_status": {"incident": "i1"},
        "list_actions": {"rule": "ClaimTask"}, "neighborhood": {"svc": "a", "depth": 2}, "rank_critical": {"k": 2},
        "brokers": {"k": 2, "sampling": 4}, "failure_domains": {}, "communities": {},
        "dependency_path": {"source": "a", "target": "c", "max_len": 4}, "why": {"uid": "svc:c"},
        "simulate": {"incident": "i1"}, "decision": {"did": "D-none"}, "optimizer_status": {}, "fedlog": {},
    }
    for name, args in calls.items():
        err, out = call(server, name, args)
        assert not err, (name, out)
    assert call(server, "whoami")[1]["role"] == "responder"


def test_invalid_arguments_are_refused_before_a_tool_runs(world):
    server = world.server()
    for name, args in (("neighborhood", {"svc": "a", "depth": "deep"}), ("neighborhood", {"svc": "a"}),
                       ("rank_critical", {"k": "many"}), ("send_notice", {"peer": "acme"}),
                       ("fedlog", {"from_pt": "yesterday"}), ("decision", {}), ("read_entity", {"uid": 7}),
                       ("neighborhood", {"svc": "a", "depth": 2, "asof": "then"})):
        err, _ = call(server, name, args)
        assert err, (name, args)
    err, _ = call(server, "fedlog", {"from_pt": -1})
    assert err
    err, _ = call(server, "rank_critical", {"k": 2, "asof": 5, "asof_seq": 1})
    assert err  # both a valid time and a seq


def test_decide_commits_through_the_executive_and_decision_reads_it_back(world):
    server = world.server()
    err, out = call(server, "decide", {"incident": "i1", "nominations": [], "rationale": "fail over", "request_id": "r1"})
    assert not err and out["outcome"] == "APPLIED", out
    err, found = call(server, "decision", {"did": out["did"]})
    assert not err and found["did"] == out["did"] and found["committed_as"] == out["pid"]
    assert any(c["won"] for c in found["candidates"])
    assert call(world.server("com-1", executive=False), "whoami")[1]["role"] == "comms"


def test_only_a_responder_may_use_the_executive_tools(world):
    server = world.server("com-1")
    for name, args in (("simulate", {"incident": "i1"}),
                       ("decide", {"incident": "i1", "nominations": [], "rationale": "x", "request_id": "r"})):
        err, _ = call(server, name, args)
        assert err, name


# ── T-F: asof routes to the scratch graphs

def scratch(world):
    return sorted(g for g in world.redis.execute_command("GRAPH.LIST") if g.startswith(world.graph.name + ":asof:"))


def test_asof_and_asof_seq_route_the_advisory_tools_to_scratch_graphs(world):
    server = world.server()
    err, live = call(server, "neighborhood", {"svc": "a", "depth": 3})
    assert not err and "svc:c" in live["downstream"]["nodes"] and scratch(world) == []
    err, then = call(server, "neighborhood", {"svc": "a", "depth": 3, "asof": 1})  # before any fact was valid
    assert not err and "svc:b" not in then["downstream"]["nodes"]
    err, then = call(server, "neighborhood", {"svc": "a", "depth": 3, "asof_seq": 1})  # before any fact was written
    assert not err and "svc:b" not in then["downstream"]["nodes"]
    assert scratch(world) == sorted([f"{world.graph.name}:asof:1", f"{world.graph.name}:asof:seq1"])
    err, now = call(server, "neighborhood", {"svc": "a", "depth": 3})
    assert now == live  # the live answer did not move
    head = world.xw.k.head()["seq"]
    err, at_head = call(server, "dependency_path", {"source": "a", "target": "c", "max_len": 4, "asof_seq": head})
    assert not err and at_head == call(server, "dependency_path", {"source": "a", "target": "c", "max_len": 4})[1]
    err, none = call(server, "dependency_path", {"source": "a", "target": "c", "max_len": 4, "asof_seq": 1})
    assert not err and none is None
    for name, args in (("rank_critical", {"k": 2}), ("brokers", {"k": 2, "sampling": 4}), ("failure_domains", {}),
                       ("communities", {})):
        for extra in ({"asof": 1}, {"asof_seq": 1}):
            err, out = call(server, name, {**args, **extra})
            assert not err, (name, extra, out)
    assert world.xw.k.head()["seq"] == head  # nothing was written


def test_without_asof_support_the_arguments_are_refused(world):
    qs = QueryService(world.xw.k, mode="replay", clock=world.xw.ir_clock)
    err, _ = call(create_server(qs, "rsp-1"), "rank_critical", {"k": 2, "asof": 5})
    assert err
    assert not call(create_server(qs, "rsp-1"), "rank_critical", {"k": 2})[0]


# ── T-C, T-D tools

def test_optimizer_status_lists_the_catalogue_and_nothing_in_flight(world):
    err, out = call(world.server(), "optimizer_status")
    assert not err and out["in_flight"] == 0 and out["optimizer_owned"] == 0
    assert out["indexes"] and all(e["origin"] == "genesis" and e["state"] for e in out["indexes"])
    assert {"key", "label", "prop", "state", "origin"} <= set(out["indexes"][0])


def test_send_notice_queues_on_the_channel_and_fedlog_shows_it(world):
    ir = world.xw.ir
    ir.ok("OpenChannel", {"dir": "out", "peer": "acme", "sys_peering": "acme|prov"}, "ops-1")
    server = world.server("com-1", executive=False)
    err, out = call(server, "send_notice", {"peer": "acme", "incident": "i1", "body": "c is degraded", "pid": "sn-1"})
    assert not err and out["outcome"] == "APPLIED", out
    assert world.xw.k.read("MATCH (x:XMsg {kind: 'notice', chan: 'out:acme'}) RETURN x.state, x.body") == \
        [["queued", "c is degraded"]]
    assert world.xw.k.read("MATCH (r:Rewrite {id: 'sn-1'}) RETURN r.write_keys, r.view_keys")[0][1] == ["Channel:out:acme"]
    assert call(server, "send_notice", {"peer": "acme", "incident": "i1", "body": "c is degraded", "pid": "sn-1"})[1][
        "outcome"] == "DUPLICATE"
    err, log = call(server, "fedlog")
    assert not err and log["count"] > 0 and {"prov"} == {e["tenant"] for e in log["entries"]}
    assert "SendNotice" in [e["rule"] for e in log["entries"]]
    err, bounded = call(server, "fedlog", {"from_pt": 10**15})
    assert not err and bounded["count"] == 0


def test_send_notice_is_refused_for_a_caller_that_may_not_send_and_for_unusable_input(world):
    world.xw.ir.ok("OpenChannel", {"dir": "out", "peer": "acme", "sys_peering": "acme|prov"}, "ops-1")
    err, out = call(world.server("rsp-1"), "send_notice", {"peer": "acme", "incident": "i1", "body": "x", "pid": "sn-2"})
    assert not err and out["outcome"] != "APPLIED"
    err, out = call(world.server("com-1", executive=False), "send_notice",
                    {"peer": "acme", "incident": "i1", "body": "", "pid": "sn-3"})
    assert err
    err, out = call(world.server("com-1", executive=False), "send_notice",
                    {"peer": "nobody", "incident": "i1", "body": "x", "pid": "sn-4"})
    assert not err and out["outcome"] != "APPLIED"  # no such channel: a named refusal, not a crash
