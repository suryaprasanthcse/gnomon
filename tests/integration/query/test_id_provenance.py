"""§10.1 (4) and §10.3: ids come from cited reads; enumerated rules take a tuple index; free text is a parameter."""

import pytest

from gnomon.query.service import TEXT_MAX

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4a]


def owner_params(ir, **over):
    p = {"s": "svc:c", "o": "team:t1", "vf": ir.clock, "supersede": False}
    p.update(over)
    return p


@pytest.fixture
def world(opened, qs):
    opened.ok("RegisterEntity.Service", {"id": "c", "name": "c", "tier": "t1", "criticality": 3, "user_facing": True},
              "ops-1")
    return opened


def test_id_absent_from_cited_reads_is_rejected_before_composition(world, qs):
    ir = world
    sess = qs.session("inv-1")
    seq = ir.k.head()["seq"]
    writes = []
    orig = ir.k.graph.query
    ir.k.graph.query = lambda q, p=None, *a, **kw: (writes.append(q), orig(q, p, *a, **kw))[1]
    out = qs.propose(sess, "AssertFact.OWNED_BY", pid="ip-1", params=owner_params(ir), cite_reads=[])
    assert out.code == "SCHEMA_REJECTED" and writes == [] and ir.k.head()["seq"] == seq
    only_s = qs.read_entity(sess, "svc:c")
    out = qs.propose(sess, "AssertFact.OWNED_BY", pid="ip-2", params=owner_params(ir), cite_reads=[only_s.read_id])
    assert out.code == "SCHEMA_REJECTED" and out.detail["missing"] == ["team:t1"] and writes == []


def test_ids_from_cited_reads_are_accepted(world, qs):
    ir = world
    sess = qs.session("inv-1")
    reads = [qs.read_entity(sess, "svc:c").read_id, qs.read_entity(sess, "team:t1").read_id]
    out = qs.propose(sess, "AssertFact.OWNED_BY", pid="ip-3", params=owner_params(ir), cite_reads=reads)
    assert out.code == "APPLIED", out
    assert ir.write_keys("ip-3") == ["OWNED_BY:svc:c:*", "OWNED_BY:*:team:t1", "OWNED_BY:*"]


def test_an_id_seen_only_in_another_sessions_read_is_not_accepted(world, qs):
    ir = world
    other = qs.session("rsp-1")
    read = qs.read_entity(other, "svc:c")
    sess = qs.session("inv-1")
    out = qs.propose(sess, "AssertFact.OWNED_BY", pid="ip-4", params=owner_params(ir), cite_reads=[read.read_id])
    assert out.code == "SCHEMA_REJECTED"


def test_fid_provenance_for_retraction(world, qs):
    ir = world
    fid = ir.owned_by_fid()
    sess = qs.session("inv-1")
    p = {"s": "svc:a", "o": "team:t1", "fid": fid, "mode": "end", "vt_end": ir.clock}
    only_s = qs.read_entity(sess, "svc:a")
    out = qs.propose(sess, "RetractFact.OWNED_BY", pid="ip-5", params=p, cite_reads=[only_s.read_id])
    assert out.code == "SCHEMA_REJECTED" and fid in out.detail["missing"]
    facts = qs.facts(sess, "svc:a")
    out = qs.propose(sess, "RetractFact.OWNED_BY", pid="ip-6", params=p, cite_reads=[facts.read_id])
    assert out.code == "APPLIED", out


def test_post_update_cites_must_come_from_a_cited_read(opened, qs):
    ir = opened
    fid = ir.owned_by_fid()
    sess = qs.session("com-1")
    params = {"incident": "i1", "message_id": "m1", "cites": [{"s": "svc:a", "fid": fid}]}
    out = qs.propose(sess, "PostUpdate", pid="ip-7", params=params, text="investigating", cite_reads=[])
    assert out.code == "SCHEMA_REJECTED"
    read = qs.list_actions(sess, "PostUpdate", {"incident": "i1"})
    out = qs.propose(sess, "PostUpdate", pid="ip-8", params=params, text="investigating", cite_reads=[read.read_id])
    assert out.code == "APPLIED", out


def test_enumerated_rules_refuse_parameters(opened, qs):
    sess = qs.session("inv-1")
    read = qs.list_actions(sess, "ClaimTask")
    out = qs.propose(sess, "ClaimTask", pid="ip-9", params={"agent": "inv-1", "task": "t1", "ttl_ms": 100},
                     cite_reads=[read.read_id])
    assert out.code == "SCHEMA_REJECTED"
    out = qs.propose(sess, "ClaimTask", pid="ip-10", tuple_index=7, cite_reads=[read.read_id])
    assert out.code == "SCHEMA_REJECTED" and "tuple_index" in out.detail["reason"]
    out = qs.propose(sess, "ClaimTask", pid="ip-11", tuple_index=0, cite_reads=[])
    assert out.code == "SCHEMA_REJECTED"


def test_tuple_must_come_from_a_read_of_the_same_rule(opened, qs):
    sess = qs.session("inv-1")
    wrong = qs.list_actions(sess, "CompleteTask")
    out = qs.propose(sess, "ClaimTask", pid="ip-12", tuple_index=0, cite_reads=[wrong.read_id])
    assert out.code == "SCHEMA_REJECTED"


def test_rules_outside_the_agent_surface_are_not_proposable(opened, qs):
    sess = qs.session("inv-1")
    read = qs.read_entity(sess, "svc:a")
    for rule in ("RegisterRule", "DeactivateRule", "RegisterAgent", "RegisterEntity.Team", "Genesis", "Observe.alert",
                 "ExpireLease", "NoSuchRule"):
        out = qs.propose(sess, rule, pid=f"ip-x-{rule}", params={}, cite_reads=[read.read_id])
        assert out.code == "SCHEMA_REJECTED", rule


def test_agent_comes_from_the_session(opened, qs):
    ir = opened
    sess = qs.session("inv-1")
    read = qs.list_actions(sess, "ClaimTask")
    out = qs.propose(sess, "ClaimTask", pid="ip-13", tuple_index=0, cite_reads=[read.read_id])
    assert out.code == "APPLIED"
    assert ir.one("MATCH (r:Rewrite {id: 'ip-13'}) RETURN r.agent")[0] == "inv-1"
    assert ir.one("MATCH (a:Agent)-[l:HOLDS]->(:Task {id: 't1'}) WHERE l.closed_seq IS NULL RETURN a.id")[0] == "inv-1"


def test_free_text_is_a_parameter_never_cypher(opened, qs):
    ir = opened
    nasty = "x'}) DETACH DELETE n //"
    sess = qs.session("com-1")
    read = qs.list_actions(sess, "PostUpdate", {"incident": "i1"})
    cites = [{"s": t["s"], "fid": t["fid"]} for t in read.tuples[:1]]
    out = qs.propose(sess, "PostUpdate", pid="ft-1", params={"incident": "i1", "message_id": "m2", "cites": cites},
                     text=nasty, cite_reads=[read.read_id])
    assert out.code == "APPLIED", out
    assert ir.one("MATCH (m:Message {id: 'm2'}) RETURN m.body")[0] == nasty
    assert ir.one("MATCH (n) RETURN count(n)")[0] > 5


def test_free_text_length_limit(opened, qs):
    sess = qs.session("com-1")
    read = qs.list_actions(sess, "PostUpdate", {"incident": "i1"})
    cites = [{"s": t["s"], "fid": t["fid"]} for t in read.tuples[:1]]
    out = qs.propose(sess, "PostUpdate", pid="ft-2", params={"incident": "i1", "message_id": "m3", "cites": cites},
                     text="x" * (TEXT_MAX + 1), cite_reads=[read.read_id])
    assert out.code == "SCHEMA_REJECTED"


def test_stale_proposal_is_rejected_not_applied(opened, qs):
    """How an agent might try to apply a stale proposal: read, let a rival write, propose anyway (§7.7)."""
    ir = opened
    sess = qs.session("inv-1")
    read = qs.list_actions(sess, "ClaimTask")
    ir.claim("t1", agent="rsp-1")
    out = qs.propose(sess, "ClaimTask", pid="st-1", tuple_index=0, cite_reads=[read.read_id])
    assert out.code == "REJECTED_STALE", out
    assert ir.one("MATCH (a:Agent)-[l:HOLDS]->(:Task {id: 't1'}) WHERE l.closed_seq IS NULL RETURN a.id")[0] == "rsp-1"


def test_advisory_reads_add_no_keys(topology, qs):
    sess = qs.session("inv-1")
    before = dict(sess.reads)
    assert qs.rank_critical(k=2) and qs.neighborhood("a", 2) and qs.failure_domains() and qs.communities()
    assert qs.brokers(k=2, sampling=6) and qs.dependency_path("a", "d", 5)
    assert sess.reads == before  # nothing recorded, so nothing citeable
    out = qs.propose(sess, "ClaimTask", pid="adv-1", tuple_index=0, cite_reads=["adv-1"])
    assert out.code == "SCHEMA_REJECTED"
