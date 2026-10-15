"""§4.3 Q1-Q7 on a hand-built ownership history with known answers.

Service z: t1 from V1 (rewrite S1), then t2 from V2 (S2, supersede), then t3 from V3 (S3, supersede). The versions
(§4.2) are s1#0 (t1, ts S1, te S2), s2#e (t1, [V1,V2), ts S2), s2#0 (t2, ts S2, te S3), s3#e (t2, [V2,V3), ts S3),
s3#0 (t3, [V3,INF), ts S3).
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g4a]

EPOCH = 1_791_000_000_000
V1, V2, V3 = EPOCH + 100, EPOCH + 200, EPOCH + 300
INF = 9007199254740991


@pytest.fixture
def history(opened, qs):
    ir = opened
    for sid in ("z",):
        ir.ok("RegisterEntity.Service", {"id": sid, "name": sid, "tier": "t1", "criticality": 3, "user_facing": True},
              "ops-1")
    for tid in ("t2", "t3"):
        ir.ok("RegisterEntity.Team", {"id": tid, "name": tid}, "ops-1")
    s1 = ir.ok("AssertFact.OWNED_BY", {"s": "svc:z", "o": "team:t1", "vf": V1, "supersede": False}, "ops-1", pid="h1").seq
    s2 = ir.ok("AssertFact.OWNED_BY", {"s": "svc:z", "o": "team:t2", "vf": V2, "supersede": True, "old_o": "team:t1"},
               "ops-1", pid="h2").seq
    s3 = ir.ok("AssertFact.OWNED_BY", {"s": "svc:z", "o": "team:t3", "vf": V3, "supersede": True, "old_o": "team:t2"},
               "ops-1", pid="h3").seq
    return ir, (s1, s2, s3), qs.session("inv-1")


def rows(qs, sess, query, args):
    return qs.as_of(sess, query, args).rows


def test_q1_owner_at_valid_time_as_known_after_a_rewrite(history, qs):
    _, (s1, s2, s3), sess = history
    one = lambda T, S: [(r["team"], r["fid"]) for r in rows(qs, sess, "Q1", {"svc": "z", "T": T, "S": S})]  # noqa: E731
    assert one(V1 + 10, s3) == [("t1", "h2#e")]
    assert one(V1 + 10, s1) == [("t1", "h1#0")]
    assert one(V2 + 10, s1) == [("t1", "h1#0")]  # after S1 the system believed t1 forever
    assert one(V2 + 10, s2) == [("t2", "h2#0")]
    assert one(V2 + 10, s3) == [("t2", "h3#e")]
    assert one(V1 - 10, s3) == []  # before z had an owner


def test_q2_current_history_ordered_by_valid_from(history, qs):
    _, _, sess = history
    got = [(r["team"], r["valid_from"], r["valid_to"], r["fid"]) for r in rows(qs, sess, "Q2", {"svc": "z"})]
    assert got == [("t1", V1, V2, "h2#e"), ("t2", V2, V3, "h3#e"), ("t3", V3, INF, "h3#0")]


def test_q3_belief_after_a_rewrite(history, qs):
    _, (s1, s2, s3), sess = history
    two = [(r["team"], r["valid_from"], r["valid_to"]) for r in rows(qs, sess, "Q3", {"svc": "z", "S": s2})]
    assert two == [("t1", V1, V2), ("t2", V2, INF)]
    one = [(r["team"], r["valid_from"], r["valid_to"]) for r in rows(qs, sess, "Q3", {"svc": "z", "S": s1})]
    assert one == [("t1", V1, INF)]


def test_q4_services_a_team_owned_at_valid_time(history, qs):
    _, _, sess = history
    assert [(r["service"], r["fid"]) for r in rows(qs, sess, "Q4", {"team": "t1", "T": V1 + 10})] == [("z", "h2#e")]
    assert [r["service"] for r in rows(qs, sess, "Q4", {"team": "t2", "T": V2 + 10})] == ["z"]
    assert rows(qs, sess, "Q4", {"team": "t1", "T": EPOCH + 250}) == []  # t1 no longer owns z; a starts later


def test_q5_change_feed_in_a_valid_time_window(history, qs):
    _, _, sess = history
    got = [(r["service"], r["team"], r["changed_at"], r["fid"])
           for r in rows(qs, sess, "Q5", {"T1": EPOCH + 50, "T2": EPOCH + 350})]
    assert got == [("z", "t1", V1, "h2#e"), ("z", "t2", V2, "h3#e"), ("z", "t3", V3, "h3#0")]
    assert rows(qs, sess, "Q5", {"T1": EPOCH + 101, "T2": EPOCH + 200}) == []


def test_q6_owners_at_the_moment_an_incident_opened(history, qs):
    ir, _, sess = history
    fid = ir.owned_by_fid()
    before = [(r["service"], r["team"], r["fid"]) for r in rows(qs, sess, "Q6", {"incident": "i1"})]
    assert before == [("a", "t1", fid)]
    ir.ok("RegisterEntity.Team", {"id": "t5", "name": "t5"}, "ops-1")
    ir.ok("AssertFact.OWNED_BY", {"s": "svc:a", "o": "team:t5", "vf": ir.clock, "supersede": True, "old_o": "team:t1"},
          "ops-1")
    after = [(r["service"], r["team"], r["fid"]) for r in rows(qs, sess, "Q6", {"incident": "i1"})]
    assert after == before  # later reassignment does not change who owned it when the incident opened


def test_as_of_reads_return_ids_for_provenance(history, qs):
    _, _, sess = history
    read = qs.as_of(sess, "Q2", {"svc": "z"})
    assert {"h3#0", "team:t3", "svc:z"} <= read.ids


def test_q7_dependency_topology(topology, qs):
    ir = topology
    sess = qs.session("inv-1")
    read = qs.as_of(sess, "Q7", {"T": ir.clock})
    edges = sorted((r["from"], r["to"], r["hard"]) for r in read.rows)
    assert edges == [("svc:a", "svc:b", True), ("svc:b", "svc:c", True), ("svc:c", "svc:d", True),
                     ("svc:e", "svc:c", True)]
    assert read.keys == ("DEPENDS_ON:*",)
