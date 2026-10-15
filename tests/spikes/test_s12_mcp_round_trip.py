"""S12 · MCP SDK round trip with tool-call logging (in-process client, MCP Python SDK 2.x)."""

import asyncio
import json

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def test_s12_tool_call_round_trip_is_schema_checked_and_logged(facts):
    calls: list[dict] = []
    server = MCPServer("spike-s12")

    @server.tool()
    def propose(rule: str, pid: str) -> dict:
        """Record a proposal and acknowledge it."""
        calls.append({"tool": "propose", "rule": rule, "pid": pid})
        return {"outcome": "APPLIED", "pid": pid}

    async def session():
        async with Client(server) as client:
            listed = await client.list_tools()
            ok = await client.call_tool("propose", {"rule": "Noop", "pid": "p-1"})
            bad = await client.call_tool("propose", {"rule": "Noop"})
            return listed, ok, bad

    listed, ok, bad = asyncio.run(session())
    schema = {t.name: t.input_schema for t in listed.tools}["propose"]
    schema_ok = schema["required"] == ["rule", "pid"]
    payload = json.loads(ok.content[0].text)
    round_trip = ok.is_error is False and payload == {"outcome": "APPLIED", "pid": "p-1"}
    logged = calls == [{"tool": "propose", "rule": "Noop", "pid": "p-1"}]  # the invalid call never reached the tool

    facts.expect("S12", schema_ok and round_trip and bad.is_error is True and logged)
