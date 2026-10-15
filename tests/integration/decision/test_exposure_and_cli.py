"""R17 is human-only: not exposed over MCP, reachable through `gk approve` and `gk veto` (master §14.5, §20)."""

import asyncio
import json

import pytest
from mcp import Client

from gnomon import cli
from gnomon.mcp.server import create_server

pytestmark = [pytest.mark.integration, pytest.mark.gate_ta]

TA_RULES = ("ApproveDecision", "RequestApproval", "RegisterScorer", "NoopAction", "NoopActionGated")


def test_r17_not_exposed_over_mcp(dw, qs):
    server = create_server(qs, agent="inv-1")

    async def tools():
        async with Client(server) as client:
            return {t.name for t in (await client.list_tools()).tools}

    names = asyncio.run(tools())
    assert not {n for n in names if "approv" in n or "veto" in n or "scorer" in n}
    sess = qs.session("hum-1")
    read = qs.read_entity(sess, "svc:a")
    for rule in TA_RULES:
        out = qs.propose(sess, rule, pid=f"mcp-{rule}", params={"aid": "x"}, cite_reads=[read.read_id])
        assert out.code == "SCHEMA_REJECTED" and "not proposable" in out.detail["reason"], rule
    assert dw.k.read("MATCH (a:Approval) RETURN count(a)") == [[0]]


def run(capsys, url, command, graph, aid, sha, at, **extra):
    argv = [command, "--url", url, "--graph", graph, "--aid", aid, "--decision-sha", sha, "--agent", "hum-1",
            "--at", str(at), "--note", "seen"]
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(("command", "verdict"), [("approve", "approve"), ("veto", "veto")])
def test_gk_approve_and_veto(dw, falkordb_container, capsys, command, verdict):
    d = dw.envelope(f"D-{verdict}")
    assert dw.request(d).code == "APPLIED"
    at = dw.k.head()["pt"] + 100
    code, out = run(capsys, falkordb_container.url, command, dw.k.graph.name, d["aid"], d["sha"], at)
    assert code == 0 and out["outcome"] == "APPLIED" and out["aid"] == d["aid"] and out["verdict"] == verdict
    assert dw.ir.one("MATCH (a:Approval {aid: $a}) RETURN a.state, a.verdict, a.approver, a.note", a=d["aid"]) == [
        "decided", verdict, "hum-1", "seen"]


def test_gk_approve_reports_a_refusal_and_exits_nonzero(dw, falkordb_container, capsys):
    d = dw.envelope("D-late")
    dw.request(d, ttl=1_000)
    at = dw.k.head()["pt"] + 5_000  # past expires_pt
    code, out = run(capsys, falkordb_container.url, "approve", dw.k.graph.name, d["aid"], d["sha"], at)
    assert code == 1 and out["outcome"] == "REJECTED_GUARD" and out["detail"]["guard"].endswith("unexpired")
    code, out = run(capsys, falkordb_container.url, "approve", dw.k.graph.name, d["aid"], "f" * 64,
                    dw.k.head()["pt"] + 100)
    assert code == 1 and out["outcome"] == "REJECTED_GUARD"
