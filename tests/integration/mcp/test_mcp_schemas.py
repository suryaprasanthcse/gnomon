"""§20 core tool catalogue over MCP: listed with schemas; propose takes only the agent-facing fields (§10.3)."""

import asyncio
import json

import pytest
from mcp import Client

from gnomon.mcp.server import create_server

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4a]

CORE = {"whoami", "read_entity", "facts", "as_of", "incident_status", "list_actions", "propose", "neighborhood",
        "rank_critical", "brokers", "failure_domains", "communities", "dependency_path", "why"}
FORBIDDEN_FIELDS = {"read_seq", "view_keys", "write_keys", "rule_sha", "agent", "$agent"}


def run(server, name, args=None):
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


def keys_of(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from keys_of(v)
    elif isinstance(value, list):
        for v in value:
            yield from keys_of(v)


@pytest.fixture
def server(opened, qs):
    return create_server(qs, agent="inv-1")


def test_every_core_tool_and_no_other_is_listed_with_a_schema(server):
    tools = listed(server)
    assert set(tools) == CORE
    for name, tool in tools.items():
        assert tool.description and tool.input_schema["type"] == "object", name


def test_input_schemas_match_the_catalogue(server):
    tools = {n: t.input_schema for n, t in listed(server).items()}
    props = {n: set(s.get("properties", {})) for n, s in tools.items()}
    asof = {"asof", "asof_seq"}  # T-F (D-71): every advisory algorithm tool takes a valid time or a seq
    assert props["whoami"] == set() and props["failure_domains"] == asof and props["communities"] == asof
    assert props["read_entity"] == {"uid"} and props["facts"] == {"uid"}
    assert props["as_of"] == {"query", "args"} and props["incident_status"] == {"incident"}
    assert props["list_actions"] == {"rule", "args"}
    assert props["neighborhood"] == {"svc", "depth"} | asof and props["rank_critical"] == {"k"} | asof
    assert props["brokers"] == {"k", "sampling"} | asof
    assert props["dependency_path"] == {"source", "target", "max_len"} | asof  # `from` is a Python keyword (D-34)
    assert props["why"] == {"uid", "pid", "did"}
    assert set(tools["as_of"]["properties"]["query"]["enum"]) == {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6"}


def test_propose_accepts_only_agent_facing_fields(server):
    schema = listed(server)["propose"].input_schema
    assert set(schema["properties"]) == {"rule", "tuple_index", "params", "text", "cite_reads", "pid"}
    assert set(schema["required"]) == {"rule", "pid"}
    assert not FORBIDDEN_FIELDS & set(schema["properties"])


def test_no_tool_exposes_kernel_fields_or_raw_cypher(server):
    for name, tool in listed(server).items():
        assert not FORBIDDEN_FIELDS & set(tool.input_schema.get("properties", {})), name
        assert "cypher" not in set(tool.input_schema.get("properties", {})), name


def test_t_layer_tools_are_absent(server):
    assert not {"simulate", "decide", "decision", "optimizer_status", "send_notice", "fedlog", "approve",
                "register_rule"} & set(listed(server))


def test_whoami_comes_from_the_session(server):
    err, who = run(server, "whoami")
    assert not err and who == {"agent": "inv-1", "role": "investigator", "skills": ["ops"]}


def test_binding_reads_hide_kernel_fields(server):
    for name, args in [("read_entity", {"uid": "svc:a"}), ("facts", {"uid": "svc:a"}),
                       ("incident_status", {"incident": "i1"}), ("list_actions", {"rule": "ClaimTask"}),
                       ("as_of", {"query": "Q2", "args": {"svc": "a"}})]:
        err, payload = run(server, name, args)
        assert not err, (name, payload)
        assert payload["read_id"] and not FORBIDDEN_FIELDS & set(keys_of(payload)), name


def test_read_ids_are_session_scoped_across_calls(server):
    _, read = run(server, "read_entity", {"uid": "svc:a"})
    _, listing = run(server, "list_actions", {"rule": "ClaimTask"})
    err, out = run(server, "propose", {"rule": "ClaimTask", "tuple_index": 0, "pid": "mcp-1",
                                       "cite_reads": [read["read_id"], listing["read_id"]]})
    assert not err and out["outcome"] == "APPLIED" and out["pid"] == "mcp-1" and out["seq"] > 0


def test_stale_and_rejected_outcomes_are_named(opened, qs, server):
    _, listing = run(server, "list_actions", {"rule": "ClaimTask"})
    opened.claim("t1", agent="rsp-1")
    _, out = run(server, "propose", {"rule": "ClaimTask", "tuple_index": 0, "pid": "mcp-2",
                                     "cite_reads": [listing["read_id"]]})
    assert out["outcome"] == "REJECTED_STALE"
    _, out = run(server, "propose", {"rule": "RegisterRule", "params": {}, "pid": "mcp-3", "cite_reads": []})
    assert out["outcome"] == "SCHEMA_REJECTED" and out["detail"]["reason"]


def test_a_supplied_agent_cannot_override_the_session(opened, qs, server):
    _, listing = run(server, "list_actions", {"rule": "ClaimTask"})
    err, out = run(server, "propose", {"rule": "ClaimTask", "tuple_index": 0, "pid": "mcp-4", "agent": "hum-1",
                                       "cite_reads": [listing["read_id"]]})
    if not err:
        assert out["outcome"] == "APPLIED"
        assert opened.one("MATCH (r:Rewrite {id: 'mcp-4'}) RETURN r.agent")[0] == "inv-1"


def test_missing_required_argument_is_a_schema_error(server):
    err, _ = run(server, "propose", {"rule": "ClaimTask"})
    assert err is True


def test_query_errors_become_tool_errors(server):
    err, _ = run(server, "as_of", {"query": "Q99", "args": {}})
    assert err is True


def test_algorithm_tools_and_why(topology, qs):
    server = create_server(qs, agent="inv-1")
    err, ranked = run(server, "rank_critical", {"k": 2})
    assert not err and {r["service"] for r in ranked} == {"c", "d"}
    err, path = run(server, "dependency_path", {"source": "a", "target": "d", "max_len": 5})
    assert not err and path["nodes"] == ["svc:a", "svc:b", "svc:c", "svc:d"]
    err, near = run(server, "neighborhood", {"svc": "a", "depth": 1})
    assert not err and near["downstream"]["nodes"] == ["svc:b"]
    assert not run(server, "brokers", {"k": 2, "sampling": 6})[0]
    assert not run(server, "failure_domains")[0] and not run(server, "communities")[0]
    err, why = run(server, "why", {"uid": "svc:a"})
    assert not err and why["entity"] == "svc:a" and why["rule"]
    err, why = run(server, "why", {"pid": "genesis"})
    assert not err and why["rule"] == "Genesis"
    assert run(server, "why", {})[0] is True  # needs uid or pid
