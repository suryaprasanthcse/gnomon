"""§10.1 (1, 2) and §7.2: every binding read is one read-only query that returns read_seq with its rows."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4a]


def spy(graph):
    """Record every ro_query and query the graph receives."""
    calls = {"ro": [], "rw": []}
    ro, rw = graph.ro_query, graph.query

    def ro_query(q, params=None, *a, **kw):
        calls["ro"].append(q)
        return ro(q, params, *a, **kw)

    def query(q, params=None, *a, **kw):
        calls["rw"].append(q)
        return rw(q, params, *a, **kw)

    graph.ro_query, graph.query = ro_query, query
    return calls


def test_read_seq_atomic(opened, qs):
    ir = opened
    calls = spy(ir.k.graph)
    sess = qs.session("inv-1")
    head_before = ir.k.head()["seq"]
    calls["ro"].clear()
    read = qs.read_entity(sess, "svc:a")
    assert read.read_seq == head_before
    assert len(calls["ro"]) == 1 and "AS read_seq" in calls["ro"][0] and calls["rw"] == []
    ir.ok("RegisterEntity.Team", {"id": "t9", "name": "t9"}, "ops-1")
    later = qs.read_entity(sess, "svc:a")
    assert later.read_seq == head_before + 1 and read.read_seq == head_before  # an earlier read keeps its snapshot seq


def test_every_binding_read_embeds_head_in_its_query(opened, qs):
    ir = opened
    calls = spy(ir.k.graph)
    sess = qs.session("inv-1")
    qs.read_entity(sess, "svc:a")
    qs.facts(sess, "svc:a")
    qs.incident_status(sess, "i1")
    qs.as_of(sess, "Q2", {"svc": "a"})
    qs.list_actions(sess, "ClaimTask")
    assert len(calls["ro"]) == 5 and all("MATCH (h:Head)" in c and "AS read_seq" in c for c in calls["ro"])
    assert calls["rw"] == []


def test_reads_are_recorded_in_the_session(opened, qs):
    sess = qs.session("inv-1")
    r1 = qs.read_entity(sess, "svc:a")
    r2 = qs.facts(sess, "svc:a")
    assert r1.read_id != r2.read_id
    assert sess.reads[r1.read_id] is r1 and sess.reads[r2.read_id] is r2
    assert r1.read_seq == r2.read_seq == opened.k.head()["seq"]


def test_sessions_do_not_share_reads(opened, qs):
    mine, other = qs.session("inv-1"), qs.session("rsp-1")
    read = qs.read_entity(mine, "svc:a")
    assert read.read_id not in other.reads
    out = qs.propose(other, "ClaimTask", pid="x-1", tuple_index=0, cite_reads=[read.read_id])
    assert out.code == "SCHEMA_REJECTED" and "unknown read" in out.detail["reason"]


def test_read_entity_rows(opened, qs):
    read = qs.read_entity(qs.session("inv-1"), "svc:a")
    (row,) = read.rows
    assert "Service" in row["labels"] and row["props"]["id"] == "a" and row["props"]["tier"] == "t1"
    assert "svc:a" in read.ids
    assert qs.read_entity(qs.session("inv-1"), "svc:nope").rows == []


def test_facts_rows(opened, qs):
    ir = opened
    fid = ir.owned_by_fid()
    read = qs.facts(qs.session("inv-1"), "svc:a")
    owned = [r for r in read.rows if r["rel"] == "OWNED_BY"]
    assert owned == [{"rel": "OWNED_BY", "outgoing": True, "other": "team:t1", "fid": fid, "since": owned[0]["since"]}]
    assert {fid, "team:t1", "svc:a"} <= read.ids


def test_incident_status_rows(opened, qs):
    ir = opened
    ir.claim("t1")
    read = qs.incident_status(qs.session("inv-1"), "i1")
    assert read.rows[0]["incident"] == "i1" and read.rows[0]["state"] == "open"
    (task,) = [r for r in read.rows if r.get("task")]
    assert (task["task"], task["state"], task["holder"]) == ("t1", "claimed", "inv-1")
