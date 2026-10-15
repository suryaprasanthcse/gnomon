"""§7.2 view-key algebra: read_seq = min, view_keys = union, phantom keys added; §20 per-tool keys."""

import pytest

from gnomon.query.session import Combined, Read, combine

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4a]


def fake(read_id, seq, keys, ids=()):
    return Read(read_id=read_id, read_seq=seq, kind="test", keys=tuple(keys), ids=frozenset(ids))


def test_combine_is_min_and_union():
    got = combine([fake("r1", 5, ["b", "a"], ["x"]), fake("r2", 3, ["c", "b"], ["y"])])
    assert got == Combined(read_seq=3, view_keys=("a", "b", "c"), ids=frozenset({"x", "y"}))


def test_combine_single_and_idempotent():
    r = fake("r1", 7, ["k:1", "k:*"], ["id"])
    assert combine([r]) == combine([r, r]) == Combined(7, ("k:*", "k:1"), frozenset({"id"}))


def test_combine_of_nothing_is_none():
    assert combine([]) is None


def test_per_tool_keys(opened, qs):
    sess = qs.session("inv-1")
    assert qs.read_entity(sess, "svc:a").keys == ("Service:a:state",)
    assert qs.read_entity(sess, "task:t1").keys == ("Task:t1:state",)
    keys = qs.facts(sess, "svc:a").keys
    fact_types = [ft["rel"] for ft in opened.k.registry.fact_types()]
    assert set(keys) == {f"{r}:svc:a:*" for r in fact_types} | {f"{r}:*:svc:a" for r in fact_types}
    assert qs.incident_status(sess, "i1").keys == ("Incident:i1:state", "Task:*")


@pytest.mark.parametrize(("query", "args", "keys"), [
    ("Q1", {"svc": "a", "T": 1, "S": 99}, ("OWNED_BY:svc:a:*",)),
    ("Q2", {"svc": "a"}, ("OWNED_BY:svc:a:*",)),
    ("Q3", {"svc": "a", "S": 99}, ("OWNED_BY:svc:a:*",)),
    ("Q4", {"team": "t1", "T": 1}, ("OWNED_BY:*",)),
    ("Q5", {"T1": 0, "T2": 5}, ("OWNED_BY:*",)),
])
def test_as_of_keys(opened, qs, query, args, keys):
    assert qs.as_of(qs.session("inv-1"), query, args).keys == keys


def test_as_of_rejects_bad_queries_and_args(opened, qs):
    from gnomon.query.service import QueryError

    sess = qs.session("inv-1")
    with pytest.raises(QueryError):
        qs.as_of(sess, "Q99", {})
    with pytest.raises(QueryError):
        qs.as_of(sess, "Q1", {"svc": "a"})  # T and S missing
    with pytest.raises(QueryError):
        qs.as_of(sess, "Q2", {"svc": "a", "extra": 1})


@pytest.mark.parametrize(("rule", "args", "phantom"), [
    ("OpenIncident", None, {"Incident:*"}),
    ("ClaimTask", None, set()),
    ("ResolveIncident", None, {"Task:*"}),
])
def test_enablement_phantom_keys(opened, qs, rule, args, phantom):
    read = qs.list_actions(qs.session("inv-1"), rule, args)
    assert phantom <= set(read.keys)


def test_proposal_read_seq_is_min_and_keys_are_union(opened, qs):
    ir = opened
    sess = qs.session("inv-1")
    old = qs.list_actions(sess, "ClaimTask")  # keys Task:t1:state, HOLDS:*:t1
    ir.ok("RegisterEntity.Team", {"id": "t9", "name": "t9"}, "ops-1")  # touches no key the read depends on
    new = qs.read_entity(sess, "svc:a")
    assert new.read_seq == old.read_seq + 1
    out = qs.propose(sess, "ClaimTask", pid="vk-1", tuple_index=0, cite_reads=[new.read_id, old.read_id])
    assert out.code == "APPLIED", out
    read_seq, view_keys = ir.one("MATCH (r:Rewrite {id: 'vk-1'}) RETURN r.read_seq, r.view_keys")
    assert read_seq == old.read_seq
    assert view_keys == sorted({"Task:t1:state", "HOLDS:*:t1", "Service:a:state"})


def test_tuple_proposal_carries_the_tuples_own_keys(opened, qs):
    ir = opened
    ir.ok("RegisterEntity.Service", {"id": "b2", "name": "b2", "tier": "t1", "criticality": 3, "user_facing": True},
          "ops-1")
    ir.alert(source_id="al-2", service="b", signature="mem")
    ir.alert(source_id="al-3", service="b2", signature="cpu")
    sess = qs.session("triage-1")
    read = qs.list_actions(sess, "OpenIncident")
    assert [t["alert"] for t in read.tuples] == ["al-2", "al-3"]
    out = qs.propose(sess, "OpenIncident", pid="vk-2", tuple_index=1, cite_reads=[read.read_id])
    assert out.code == "APPLIED", out
    keys = ir.one("MATCH (r:Rewrite {id: 'vk-2'}) RETURN r.view_keys")[0]
    assert keys == sorted(["Alert:al-3:state", "Incident:b2:cpu", "Incident:*"])  # no keys of tuple 0 (al-2)
