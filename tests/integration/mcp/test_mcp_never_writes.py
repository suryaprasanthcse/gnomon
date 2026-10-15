"""§10.3 rule 1: no tool writes except propose, and propose writes only through the kernel service."""

import asyncio

import pytest
from mcp import Client

from gnomon.footprint.verify import canonical_state
from gnomon.mcp.server import create_server

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4a]

READ_CALLS = [
    ("whoami", {}), ("read_entity", {"uid": "svc:a"}), ("facts", {"uid": "svc:a"}),
    ("as_of", {"query": "Q2", "args": {"svc": "a"}}), ("incident_status", {"incident": "i1"}),
    ("list_actions", {"rule": "ClaimTask"}), ("list_actions", {"rule": "OpenIncident"}),
    ("neighborhood", {"svc": "a", "depth": 2}), ("rank_critical", {"k": 3}), ("brokers", {"k": 3, "sampling": 6}),
    ("failure_domains", {}), ("communities", {}), ("dependency_path", {"source": "a", "target": "d", "max_len": 4}),
    ("why", {"uid": "svc:a"}), ("why", {"pid": "genesis"}),
]


async def call_all(server, calls):
    async with Client(server) as client:
        return [await client.call_tool(name, args) for name, args in calls]


def test_mcp_never_writes_except_propose(topology, qs):
    ir = topology
    server = create_server(qs, agent="inv-1")
    graph = ir.k.graph
    writes = []
    orig = graph.query
    graph.query = lambda q, p=None, *a, **kw: (writes.append(q), orig(q, p, *a, **kw))[1]
    before, seq = canonical_state(graph, ir.client), ir.k.head()["seq"]

    results = asyncio.run(call_all(server, READ_CALLS))

    assert all(r.is_error is False for r in results)
    assert writes == [] and ir.k.head()["seq"] == seq
    assert canonical_state(graph, ir.client) == before


def test_propose_is_the_only_write_and_it_is_one_kernel_query(topology, qs):
    ir = topology
    server = create_server(qs, agent="inv-1")
    graph = ir.k.graph
    writes = []
    orig = graph.query
    graph.query = lambda q, p=None, *a, **kw: (writes.append(q), orig(q, p, *a, **kw))[1]

    async def flow():
        async with Client(server) as client:
            listing = (await client.call_tool("list_actions", {"rule": "ClaimTask"})).content[0].text
            import json

            read_id = json.loads(listing)["read_id"]
            return await client.call_tool("propose", {"rule": "ClaimTask", "tuple_index": 0, "pid": "nw-1",
                                                      "cite_reads": [read_id]})

    seq = ir.k.head()["seq"]
    result = asyncio.run(flow())
    assert result.is_error is False and len(writes) == 1 and ir.k.head()["seq"] == seq + 1
