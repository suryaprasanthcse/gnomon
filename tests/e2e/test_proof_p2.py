"""P2 · Hallucination and staleness firewall (master §26): models cannot corrupt memory or act on changed facts."""

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.gate_g4b]


def test_an_invented_id_is_rejected_before_composition(opened, qs):
    ir = opened
    sess = qs.session("inv-1")
    read = qs.read_entity(sess, "svc:a")
    head = ir.k.head()["seq"]
    out = qs.propose(sess, "AssertFact.OWNED_BY", pid="p2-bogus", cite_reads=[read.read_id],
                     params={"s": "svc:a", "o": "team:hallucinated", "vf": ir.clock, "supersede": False})
    assert out.code == "SCHEMA_REJECTED" and out.detail["missing"] == ["team:hallucinated"]
    assert ir.k.head()["seq"] == head  # nothing reached the kernel


def test_a_retraction_makes_comms_proposal_stale_and_names_the_key_and_rewrite(opened, qs):
    ir = opened
    fid = ir.owned_by_fid()
    sess = qs.session("com-1")
    read = qs.list_actions(sess, "PostUpdate", {"incident": "i1"})
    cite = next(t for t in read.tuples if t["rel"] == "OWNED_BY")
    ir.ok("RetractFact.OWNED_BY", {"s": "svc:a", "o": "team:t1", "fid": fid, "mode": "end", "vt_end": ir.clock},
          "ops-1", pid="p2-retract")
    retract_seq = ir.one("MATCH (r:Rewrite {id: 'p2-retract'}) RETURN r.seq")[0]
    assert retract_seq == read.read_seq + 1  # the comms agent read at seq N and the retraction is N+1
    out = qs.propose(sess, "PostUpdate", pid="p2-post", cite_reads=[read.read_id], text="owner is team t1",
                     params={"incident": "i1", "message_id": "m-p2", "cites": [{"s": cite["s"], "fid": cite["fid"]}]})
    assert out.code == "REJECTED_STALE"
    (hit,) = [s for s in out.detail["stale"] if s["key"] == "OWNED_BY:svc:a:*"]
    assert (hit["by_rewrite"], hit["changed_at"], hit["by_agent"], hit["by_rule"]) == (
        "p2-retract", retract_seq, "ops-1", "RetractFact.OWNED_BY")
    assert ir.k.read("MATCH (m:Message {id: 'm-p2'}) RETURN count(m)") == [[0]]
