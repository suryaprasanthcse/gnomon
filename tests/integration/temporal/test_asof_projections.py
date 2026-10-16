"""A15 · gate TF · Temporal projection graphs and counterfactual time travel (master §19, §4.3 Q7, §10.5, §26 row F).

Against the real FalkorDB (contract C5). `hist` is the incident world with a dependency history written at explicit valid
times, so the topology differs at every probe time and after every probe seq:

    seq  fact                        valid time        what it does to the believed topology
    s1   a -> b            (hard)    from 1000         present from the start
    s2   b -> c            (hard)    from 2000         joins
    s3   d -> e            (soft)    from 1000         joins, then s6 voids it (never believed again)
    s4   c -> d            (hard)    from 3000         joins
    s5   b -> c  ended at 2800       [2000, 2800)      leaves the current valid time
    s6   d -> e  voided                                leaves the belief
    s7   e -> c            (hard)    from 3200         joins
"""

from __future__ import annotations

import json
from collections import deque

import pytest

from tests.support.query_world import register_services, replay_clock

pytestmark = [pytest.mark.integration, pytest.mark.gate_tf]

INF = 9007199254740991
EDGE = "MATCH (a:Service)-[p:DEP_NOW]->(b) RETURN a.uid, b.uid, p.hard, p.fid"
OWNER = "MATCH (s:Service)-[p:OWNER_NOW]->(t:Team) RETURN s.uid, t.uid, p.fid"
# Believed topology by valid time T (current belief): T -> {(from, to)}
AT_TIME = {
    1500: {("svc:a", "svc:b")},
    2500: {("svc:a", "svc:b"), ("svc:b", "svc:c")},
    2900: {("svc:a", "svc:b")},
    3100: {("svc:a", "svc:b"), ("svc:c", "svc:d")},
    3500: {("svc:a", "svc:b"), ("svc:c", "svc:d"), ("svc:e", "svc:c")},
}


class Hist:
    def __init__(self, ir) -> None:
        self.ir = ir
        self.seq: dict[str, int] = {}
        register_services(ir)

    def dep(self, name, s, o, vf, hard=True):
        out = self.ir.ok("AssertFact.DEPENDS_ON", {"s": f"svc:{s}", "o": f"svc:{o}", "vf": vf, "supersede": False,
                                                    "hard": hard}, "ops-1", pid=name)
        self.seq[name] = out.seq

    def fid(self, s, o) -> str:
        return self.ir.one("MATCH (:Service {id: $s})-[f:DEPENDS_ON]->(:Service {id: $o}) WHERE f.te = $inf "
                           "RETURN f.fid", s=s, o=o, inf=INF)[0]

    def retract(self, name, s, o, **kw):
        out = self.ir.ok("RetractFact.DEPENDS_ON", {"s": f"svc:{s}", "o": f"svc:{o}", "fid": self.fid(s, o), **kw},
                         "ops-1", pid=name)
        self.seq[name] = out.seq

    @property
    def graph(self):
        return self.ir.k.graph

    @property
    def name(self) -> str:
        return self.graph.name


@pytest.fixture
def hist(opened) -> Hist:
    h = Hist(opened)
    h.dep("s1", "a", "b", 1000)
    h.dep("s2", "b", "c", 2000)
    h.dep("s3", "d", "e", 1000, hard=False)
    h.dep("s4", "c", "d", 3000)
    h.retract("s5", "b", "c", mode="end", vt_end=2800)
    h.retract("s6", "d", "e", mode="void")
    h.dep("s7", "e", "c", 3200)
    return h


def pairs(graph, text=EDGE) -> set[tuple[str, str]]:
    return {(r[0], r[1]) for r in graph.ro_query(text).result_set}


def q7(h: Hist, t: int) -> set[tuple]:
    """Q7 (master §4.3) on the live graph: the dependency topology at valid time t, current belief."""
    from gnomon.query.service import _query_text

    rows = h.graph.ro_query(_query_text("reads", "q7"), {"T": t, "INF": INF}).result_set
    return {(r[1], r[2], r[3], r[4]) for r in rows}  # from, to, hard, fid


def scratch_edges(db, key: str) -> set[tuple]:
    return {tuple(r) for r in db.select_graph(key).ro_query(EDGE).result_set}


class Wall:
    """A settable wall clock in epoch milliseconds."""

    def __init__(self, now=0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def tools(h: Hist, db, redis_raw, wall):
    from gnomon.mcp.tools import Tools
    from gnomon.query.service import QueryService
    from gnomon.temporal.asof import AsofGraphs

    qs = QueryService(h.ir.k, mode="replay", clock=replay_clock(h.ir))
    return Tools(qs, qs.session("inv-1"), None, AsofGraphs(db, redis_raw, h.name, wall))


# ── the build protocol (§19.1)

@pytest.mark.parametrize("t", sorted(AT_TIME))
def test_asof_projection_equals_q7(hist, db, redis_raw, t):
    from gnomon.temporal import asof

    key = asof.build(db, redis_raw, hist.name, at=t, now=Wall())
    assert key == f"{hist.name}:asof:{t}"
    assert {(a, b) for a, b, *_ in scratch_edges(db, key)} == AT_TIME[t]
    assert scratch_edges(db, key) == q7(hist, t)  # same edges, same hard flag, same fid as the bitemporal facts


def test_asof_projection_owners_equal_the_ownership_facts(hist, db, redis_raw):
    from gnomon.temporal import asof

    hist.ir.ok("RegisterEntity.Team", {"id": "t9", "name": "t9"}, "ops-1")
    hist.ir.ok("AssertFact.OWNED_BY", {"s": "svc:b", "o": "team:t9", "vf": 2000, "supersede": False}, "ops-1")
    live = "MATCH (s:Service)-[f:OWNED_BY]->(t:Team) WHERE f.te = $INF AND f.vf <= $T AND $T < f.vt RETURN s.uid, t.uid, f.fid"
    for t in (500, 1500, 2500, 3500, hist.ir.clock):
        key = asof.build(db, redis_raw, hist.name, at=t, now=Wall())
        expected = {tuple(r) for r in hist.graph.ro_query(live, {"T": t, "INF": INF}).result_set}
        assert {tuple(r) for r in db.select_graph(key).ro_query(OWNER).result_set} == expected
    early = {r[:2] for r in db.select_graph(f"{hist.name}:asof:1500").ro_query(OWNER).result_set}
    assert ("svc:b", "team:t9") not in early  # b was not owned by t9 before valid time 2000


def test_current_edges_created_after_t_do_not_bleed_into_the_projected_graph(hist, db, redis_raw):
    from gnomon.temporal import asof

    live_now = pairs(hist.graph)
    assert ("svc:e", "svc:c") in live_now and ("svc:c", "svc:d") in live_now  # the live topology has both
    key = asof.build(db, redis_raw, hist.name, at=1500, now=Wall())
    assert pairs(db.select_graph(key)) == {("svc:a", "svc:b")}
    # A fact written after the scratch graph exists is in the live graph only.
    hist.dep("late", "a", "e", 1000)
    assert ("svc:a", "svc:e") in pairs(hist.graph)
    assert ("svc:a", "svc:e") not in pairs(db.select_graph(key))
    # ...and rebuilding at the same valid time through a fresh scratch graph does see a fact valid at 1500.
    again = asof.build(db, redis_raw, hist.name, at=1600, now=Wall())
    assert ("svc:a", "svc:e") in pairs(db.select_graph(again))


def test_building_changes_nothing_in_the_live_graph(hist, db, redis_raw):
    from gnomon.audit.runner import audit
    from gnomon.temporal import asof

    before = hist.ir.k.head()
    live_edges = pairs(hist.graph)
    asof.build(db, redis_raw, hist.name, at=2500, now=Wall())
    asof.build(db, redis_raw, hist.name, seq=hist.seq["s3"], now=Wall())
    assert hist.ir.k.head() == before and pairs(hist.graph) == live_edges
    assert audit(db, redis_raw, hist.name, ["K1", "K2"]).ok  # the scratch graphs are copies, not part of the ledger


def test_asof_seq_variant(hist, db, redis_raw):
    from gnomon.temporal import asof

    s = hist.seq
    expected = {
        s["s1"]: {("svc:a", "svc:b")},
        s["s2"]: {("svc:a", "svc:b"), ("svc:b", "svc:c")},
        s["s3"]: {("svc:a", "svc:b"), ("svc:b", "svc:c"), ("svc:d", "svc:e")},
        s["s4"]: {("svc:a", "svc:b"), ("svc:b", "svc:c"), ("svc:d", "svc:e"), ("svc:c", "svc:d")},
        s["s5"]: {("svc:a", "svc:b"), ("svc:d", "svc:e"), ("svc:c", "svc:d")},  # b -> c ended (vt = 2800): not current
        s["s6"]: {("svc:a", "svc:b"), ("svc:c", "svc:d")},  # d -> e was voided: no longer believed
        s["s7"]: {("svc:a", "svc:b"), ("svc:c", "svc:d"), ("svc:e", "svc:c")},
    }
    for seq, edges in expected.items():
        key = asof.build(db, redis_raw, hist.name, seq=seq, now=Wall())
        assert key == f"{hist.name}:asof:seq{seq}"
        assert pairs(db.select_graph(key)) == edges, seq
    assert pairs(db.select_graph(f"{hist.name}:asof:seq{s['s7']}")) == pairs(hist.graph)  # at the head: the live topology


def test_the_same_request_reuses_the_scratch_graph_and_a_half_built_one_is_rebuilt(hist, db, redis_raw):
    from gnomon.temporal import asof

    key = asof.build(db, redis_raw, hist.name, at=2500, now=Wall())
    db.select_graph(key).query("MATCH ()-[p:DEP_NOW]->() DELETE p")  # tamper: a reuse would show it
    assert pairs(db.select_graph(asof.build(db, redis_raw, hist.name, at=2500, now=Wall()))) == set()
    db.select_graph(key).query("MATCH (m:AsofMeta) DELETE m")  # no ready marker: a build that never finished
    assert pairs(db.select_graph(asof.build(db, redis_raw, hist.name, at=2500, now=Wall()))) == AT_TIME[2500]


@pytest.mark.parametrize("kw", [{}, {"at": 1, "seq": 1}, {"at": -1}, {"at": True}, {"seq": 1.5}, {"at": INF}])
def test_a_request_names_exactly_one_non_negative_integer_position(hist, db, redis_raw, kw):
    from gnomon.temporal import asof

    with pytest.raises(asof.AsofError):
        asof.build(db, redis_raw, hist.name, now=Wall(), **kw)
    assert [g for g in redis_raw.execute_command("GRAPH.LIST") if g.startswith(hist.name + ":asof:")] == []


# ── algorithms on the scratch graph (§19.2)

def bfs(edges, start, depth, forward=True):
    adj: dict[str, set] = {}
    for a, b in edges:
        x, y = (a, b) if forward else (b, a)
        adj.setdefault(x, set()).add(y)
    seen, queue = {start}, deque([(start, 0)])
    while queue:
        node, d = queue.popleft()
        if d < depth:
            for nxt in sorted(adj.get(node, ())):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, d + 1))
    return seen


def components(edges):
    adj: dict[str, set] = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen, out = set(), []
    for node in sorted(adj):
        if node not in seen:
            comp = bfs(edges, node, 99) | bfs(edges, node, 99, forward=False)
            comp |= {m for n in comp for m in adj[n]}
            seen |= comp
            out.append(sorted(comp))
    return out


def test_algorithms_on_asof_graph_equal_a_reference_computed_from_q7_topology(hist, db, redis_raw):
    t = tools(hist, db, redis_raw, Wall())
    for at in (1500, 2500, 3500):
        edges = {(a, b) for a, b, *_ in q7(hist, at)}
        near = t.neighborhood("a", 3, asof=at)
        assert set(near["downstream"]["nodes"]) - {"svc:a"} == bfs(edges, "svc:a", 3) - {"svc:a"}, at
        assert set(near["upstream"]["nodes"]) - {"svc:a"} == bfs(edges, "svc:a", 3, forward=False) - {"svc:a"}, at
        near_c = t.neighborhood("c", 2, asof=at)
        assert set(near_c["downstream"]["nodes"]) - {"svc:c"} == bfs(edges, "svc:c", 2) - {"svc:c"}, at
        assert set(near_c["upstream"]["nodes"]) - {"svc:c"} == bfs(edges, "svc:c", 2, forward=False) - {"svc:c"}, at
        domains = {tuple(sorted(s.removeprefix("svc:") for s in d["services"])) for d in t.failure_domains(asof=at)}
        reference = {tuple(sorted(x.removeprefix("svc:") for x in comp)) for comp in components(edges)}
        assert {d for d in domains if len(d) > 1} == {r for r in reference if len(r) > 1}, at


def test_the_same_tool_on_the_live_graph_and_on_the_asof_graph_differ_where_the_history_differs(hist, db, redis_raw):
    t = tools(hist, db, redis_raw, Wall())
    live = t.neighborhood("a", 3)["downstream"]["nodes"]
    then = t.neighborhood("a", 3, asof=2500)["downstream"]["nodes"]
    assert "svc:c" in then and "svc:c" not in live  # b -> c was a dependency at 2500 and is not any more
    assert t.neighborhood("a", 3, asof=INF - 1) == t.neighborhood("a", 3)  # far in the future: the live topology
    path_then = t.dependency_path("a", "c", 4, asof=2500)
    assert path_then is not None and path_then["nodes"][0] == "svc:a" and path_then["nodes"][-1] == "svc:c"
    assert t.dependency_path("a", "c", 4) is None
    ranked_then = t.rank_critical(5, asof=2500)
    assert ranked_then[0]["service"] == "c"  # the sink of a -> b -> c is the most depended upon
    assert t.rank_critical(5) != ranked_then
    assert t.communities(asof=2500) != t.communities()
    assert t.brokers(3, 6, asof=2500)[0]["service"] == "b"  # the only service on the path a -> b -> c


def test_asof_seq_reaches_every_advisory_tool(hist, db, redis_raw):
    t = tools(hist, db, redis_raw, Wall())
    seq = hist.seq["s2"]
    assert "svc:c" in t.neighborhood("a", 3, asof_seq=seq)["downstream"]["nodes"]
    assert "svc:c" not in t.neighborhood("a", 3, asof_seq=hist.seq["s1"])["downstream"]["nodes"]
    assert t.dependency_path("a", "c", 4, asof_seq=seq) is not None
    assert t.rank_critical(5, asof_seq=seq)[0]["service"] == "c"
    assert t.failure_domains(asof_seq=seq) and t.communities(asof_seq=seq) and t.brokers(2, 6, asof_seq=seq)


def test_asof_arguments_are_refused_when_unusable(hist, db, redis_raw):
    from gnomon.mcp.tools import Tools
    from gnomon.query.service import QueryError, QueryService

    t = tools(hist, db, redis_raw, Wall())
    with pytest.raises(QueryError):
        t.rank_critical(3, asof=1500, asof_seq=hist.seq["s1"])
    with pytest.raises(QueryError):
        t.rank_critical(3, asof=-5)
    bare = Tools(QueryService(hist.ir.k, mode="replay", clock=replay_clock(hist.ir)), None)
    with pytest.raises(QueryError):
        bare.rank_critical(3, asof=1500)
    assert bare.rank_critical(3) == t.rank_critical(3)


def test_the_mcp_tools_take_asof_and_asof_seq(hist, db, redis_raw):
    import asyncio

    from mcp import Client

    from gnomon.mcp.server import create_server
    from gnomon.query.service import QueryService
    from gnomon.temporal.asof import AsofGraphs

    qs = QueryService(hist.ir.k, mode="replay", clock=replay_clock(hist.ir))
    server = create_server(qs, "inv-1", asof=AsofGraphs(db, redis_raw, hist.name, Wall()))

    async def call(name, args):
        async with Client(server) as client:
            result = await client.call_tool(name, args)
            return result.is_error, json.loads(result.content[0].text)

    err, near = asyncio.run(call("neighborhood", {"svc": "a", "depth": 3, "asof": 2500}))
    assert not err and "svc:c" in near["downstream"]["nodes"]
    err, near = asyncio.run(call("neighborhood", {"svc": "a", "depth": 3, "asof_seq": hist.seq["s1"]}))
    assert not err and "svc:c" not in near["downstream"]["nodes"]
    err, now = asyncio.run(call("neighborhood", {"svc": "a", "depth": 3}))
    assert not err and "svc:c" not in now["downstream"]["nodes"]


# ── the sweeper (§19.2)

def scratch(redis_raw, hist) -> list[str]:
    return sorted(g for g in redis_raw.execute_command("GRAPH.LIST") if g.startswith(hist.name + ":asof:"))


def test_sweeper_deletes_after_ttl(hist, db, redis_raw):
    from gnomon.ops.sweeper import Sweeper
    from gnomon.temporal import asof

    assert asof.ASOF_TTL_MS == 600_000
    wall = Wall(1_000)
    by_time = asof.build(db, redis_raw, hist.name, at=2500, now=wall)
    by_seq = asof.build(db, redis_raw, hist.name, seq=hist.seq["s2"], now=wall)
    assert scratch(redis_raw, hist) == sorted([by_time, by_seq])
    sweeper = Sweeper(hist.ir.k, wall, redis_raw)
    wall.now = 1_000 + asof.ASOF_TTL_MS - 1
    assert sweeper.sweep()["asof_deleted"] == [] and scratch(redis_raw, hist) == sorted([by_time, by_seq])
    wall.now = 1_000 + asof.ASOF_TTL_MS
    assert sorted(sweeper.sweep()["asof_deleted"]) == sorted([by_time, by_seq])
    assert scratch(redis_raw, hist) == []
    assert redis_raw.hgetall(hist.name + ":asofstate") == {}
    assert hist.graph.ro_query("MATCH (h:Head) RETURN h.seq").result_set  # the live graph is untouched


def test_sweeper_keeps_a_graph_that_is_still_being_used_and_spares_other_scratch_graphs(hist, db, redis_raw):
    from gnomon.ops.sweeper import Sweeper
    from gnomon.temporal import asof

    wall = Wall(0)
    key = asof.build(db, redis_raw, hist.name, at=2500, now=wall)
    replay_key = f"{hist.name}:replay:3"
    redis_raw.execute_command("GRAPH.COPY", hist.name, replay_key)
    wall.now = 500_000
    asof.build(db, redis_raw, hist.name, at=2500, now=wall)  # a reader asks again: the age starts over
    sweeper = Sweeper(hist.ir.k, wall, redis_raw)
    wall.now = 1_000_000
    assert sweeper.sweep()["asof_deleted"] == []
    wall.now = 1_100_000
    assert sweeper.sweep()["asof_deleted"] == [key]
    assert replay_key in redis_raw.execute_command("GRAPH.LIST")  # replay graphs are not the sweeper's


def test_sweeper_deletes_a_scratch_graph_nothing_recorded(hist, db, redis_raw):
    from gnomon.ops.sweeper import Sweeper

    orphan = f"{hist.name}:asof:12345"
    redis_raw.execute_command("GRAPH.COPY", hist.name, orphan)
    assert Sweeper(hist.ir.k, Wall(5), redis_raw).sweep()["asof_deleted"] == [orphan]
    assert scratch(redis_raw, hist) == []


def test_sweeper_without_a_client_stays_read_only(hist, db, redis_raw):
    from gnomon.ops.sweeper import Sweeper
    from gnomon.temporal import asof

    key = asof.build(db, redis_raw, hist.name, at=2500, now=Wall(0))
    out = Sweeper(hist.ir.k, Wall(10 * asof.ASOF_TTL_MS)).sweep()
    assert "asof_deleted" not in out and key in redis_raw.execute_command("GRAPH.LIST")


# ── counterfactual time travel (§19.3)

@pytest.fixture
def cf(opened, db, redis_raw):
    """A decision made once the replica c-r1 was healthy; `before` is the log position at which it still was not."""
    from tests.support.exec_world import build

    xw = build(opened, db, redis_raw, replicas=("degraded", "down"))
    before = xw.k.head()["seq"]
    xw.ir.ok("Observe.health", {"source_id": "h-r1-up", "service": "c-r1", "health": "ok",
                                "observed_pt": xw.ir.clock}, "ing-1")
    res = xw.executive().decide("i1", [], "fail over", "r1")
    assert res.outcome.code == "APPLIED" and res.winner["rule"] == "FailoverService"
    return xw, res, before


def test_counterfactual_differs_when_the_world_differed(cf, db, redis_raw):
    from gnomon.temporal.counterfactual import counterfactual

    xw, res, before = cf
    head = xw.k.head()
    out = counterfactual(db, redis_raw, xw.k.graph.name, res.did, before)
    assert "error" not in out, out
    assert out["original"]["winner"]["rule"] == "FailoverService"
    assert out["original"]["read_seq"] == res.envelope["read_seq"]
    # At `before` the replica was degraded: the failover is pruned, so the best of what is left is isolation.
    assert out["counterfactual"]["read_seq"] == before
    assert out["counterfactual"]["winner"]["rule"] == "IsolateService" and out["differs"] is True
    rows = {(c["rule"], c["params"].get("replica")): c for c in out["counterfactual"]["candidates"]}
    assert rows[("FailoverService", "c-r1")]["outcome"] == "guard_pruned"
    # Same candidate set, k for k, as the recorded decision.
    assert [(c["k"], c["rule"], c["params"]) for c in out["counterfactual"]["candidates"]] == \
           [(c["k"], c["rule"], c["params"]) for c in out["original"]["candidates"]]
    assert xw.k.head() == head  # nothing was written to the live graph
    assert xw.sim_graphs() == [] and not any(":sim:cf" in g for g in redis_raw.execute_command("GRAPH.LIST"))


def test_counterfactual_at_the_decisions_own_position_reproduces_it(cf, db, redis_raw):
    from gnomon.temporal.counterfactual import counterfactual

    xw, res, _ = cf
    out = counterfactual(db, redis_raw, xw.k.graph.name, res.did, res.envelope["read_seq"])
    assert out["differs"] is False
    assert out["counterfactual"]["candidates"] == out["original"]["candidates"]  # every integer, row for row
    assert out["counterfactual"]["winner"] == out["original"]["winner"]


def test_counterfactual_names_what_it_cannot_do(cf, db, redis_raw):
    from gnomon.temporal.counterfactual import counterfactual

    xw, res, before = cf
    live = xw.k.graph.name
    assert "no committed action decision" in counterfactual(db, redis_raw, live, "D-nope", before)["error"]
    for bad in (0, xw.k.head()["seq"] + 1, -3):
        assert "--at-seq must be between" in counterfactual(db, redis_raw, live, res.did, bad)["error"]
    # Before the incident was opened it has no affected service to score.
    first = xw.k.read("MATCH (i:Incident {id: 'i1'})-[:AFFECTS]->() RETURN i.opened_seq")[0][0]
    out = counterfactual(db, redis_raw, live, res.did, first - 1)
    assert "error" in out and "counterfactual" not in out
    assert not any(":sim:cf" in g for g in redis_raw.execute_command("GRAPH.LIST"))


def test_gk_counterfactual_prints_both_tables(cf, db, redis_raw, capsys):
    from gnomon import cli

    xw, res, before = cf
    kw = redis_raw.connection_pool.connection_kwargs
    argv = ["counterfactual", res.did, "--graph", xw.k.graph.name, "--at-seq", str(before),
            "--url", f"{kw['host']}:{kw['port']}"]
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["differs"] is True and out["original"]["winner"]["rule"] == "FailoverService"
    assert out["counterfactual"]["winner"]["rule"] == "IsolateService"
    assert cli.main([*argv[:-3], "0", "--url", argv[-1]]) == 1
    assert "error" in json.loads(capsys.readouterr().out)
