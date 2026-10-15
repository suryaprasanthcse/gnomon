"""I7 and I9 (master §0.4) under random assert / supersede / end / void sequences, checked by Cypher after each step.

I7: no two current versions of a functional fact overlap in valid time for one subject; for set facts,
    none overlap for one (subject, object) pair.
I9: projection edges equal the current facts with open-ended validity (same endpoints, fid and attributes).
Proposals may be rejected; the invariants must hold after every step either way. Each example runs on its
own GRAPH.COPY of a base tenant that already holds the entities.
"""

import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

pytestmark = [pytest.mark.property, pytest.mark.gate_g1c]

INF = 9007199254740991
EPOCH = 1_791_000_000_000
SERVICES = ["svc:a", "svc:b", "svc:c"]
TEAMS = ["team:t1", "team:t2"]
REL = {"OWNED_BY": ("OWNER_NOW", "functional"), "DEPENDS_ON": ("DEP_NOW", "set")}

T = st.integers(min_value=0, max_value=12).map(lambda k: EPOCH + 10 * k)  # few instants, so collisions are common
OP = st.one_of(
    st.tuples(st.just("assert"), st.sampled_from(sorted(REL)), st.integers(0, 2), st.integers(0, 2), T,
              st.booleans(), st.booleans()),
    st.tuples(st.just("end"), st.sampled_from(sorted(REL)), st.integers(0, 9), T),
    st.tuples(st.just("void"), st.sampled_from(sorted(REL)), st.integers(0, 9)),
)


@pytest.fixture(scope="module")
def base(db, redis_raw, golden_genesis, genesis_config):
    from gnomon.kernel.service import KernelHandle

    key = f"t:test-facts-{uuid.uuid4().hex[:8]}"
    redis_raw.execute_command("GRAPH.COPY", golden_genesis, key)
    k = KernelHandle(db.select_graph(key), genesis_config["registry"])
    for i, sid in enumerate(s.split(":")[1] for s in SERVICES):
        assert k.submit("RegisterEntity.Service", {"id": sid, "name": sid, "tier": "t1", "criticality": 3,
                                                   "user_facing": False}, pid=f"rs{i}", agent="ops-1").applied
    for i, tid in enumerate(t.split(":")[1] for t in TEAMS):
        assert k.submit("RegisterEntity.Team", {"id": tid, "name": tid}, pid=f"rt{i}", agent="ops-1").applied
    yield key
    redis_raw.execute_command("GRAPH.DELETE", key)


def check_invariants(graph):
    for rel, (proj, card) in REL.items():
        same = "(s)-[b:%s]->()" % rel if card == "functional" else "(s)-[b:%s]->(o)" % rel
        overlaps = graph.query(
            f"MATCH (s)-[a:{rel}]->(o), {same} WHERE a.te = $inf AND b.te = $inf AND a.fid < b.fid "
            "AND a.vf < b.vt AND b.vf < a.vt RETURN a.fid, b.fid", {"inf": INF}).result_set
        assert overlaps == [], f"I7 broken for {rel}: {overlaps}"
        facts = graph.query(f"MATCH (s)-[f:{rel}]->(o) WHERE f.te = $inf AND f.vt = $inf "
                            "RETURN s.uid, o.uid, f.fid, f.hard ORDER BY f.fid", {"inf": INF}).result_set
        projected = graph.query(f"MATCH (s)-[p:{proj}]->(o) RETURN s.uid, o.uid, p.fid, p.hard ORDER BY p.fid").result_set
        assert projected == facts, f"I9 broken for {rel}: projection {projected} != facts {facts}"


@settings(max_examples=50, deadline=None)
@given(ops=st.lists(OP, min_size=1, max_size=14))
def test_random_sequences_keep_i7_and_i9(db, redis_raw, base, genesis_config, ops):
    from gnomon.kernel.service import KernelHandle

    key = f"{base}:ex{uuid.uuid4().hex[:8]}"
    redis_raw.execute_command("GRAPH.COPY", base, key)
    graph = db.select_graph(key)
    k = KernelHandle(graph, genesis_config["registry"])
    try:
        for n, op in enumerate(ops):
            pid = f"op{n}"
            if op[0] == "assert":
                _, rel, si, oi, vf, supersede, hard = op
                s = SERVICES[si]
                o = TEAMS[oi % len(TEAMS)] if rel == "OWNED_BY" else SERVICES[oi]
                p = {"s": s, "o": o, "vf": vf, "supersede": supersede}
                if rel == "DEPENDS_ON":
                    p["hard"] = hard
                if supersede:
                    open_o = graph.query(f"MATCH (x {{uid: $s}})-[f:{rel}]->(y) WHERE f.te = $inf AND f.vt = $inf "
                                         "RETURN y.uid", {"s": s, "inf": INF}).result_set
                    if open_o:
                        p["old_o"] = open_o[0][0]
                k.submit(f"AssertFact.{rel}", p, pid=pid, agent="ops-1")
            else:
                rel = op[1]
                current = graph.query(f"MATCH (x)-[f:{rel}]->(y) WHERE f.te = $inf RETURN x.uid, y.uid, f.fid "
                                      "ORDER BY f.fid", {"inf": INF}).result_set
                if current:
                    s, o, fid = current[op[2] % len(current)]
                    p = {"s": s, "o": o, "fid": fid, "mode": op[0]}
                    if op[0] == "end":
                        p["vt_end"] = op[3]
                    k.submit(f"RetractFact.{rel}", p, pid=pid, agent="ops-1")
            check_invariants(graph)
    finally:
        redis_raw.execute_command("GRAPH.DELETE", key)
