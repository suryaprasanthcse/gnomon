"""Bitemporal facts (master §4.1, §4.2, §9.3, §9.4): one test per §4.2 row and shape (FP, F, SP, S).

Shapes: FP = OWNED_BY (functional, projection OWNER_NOW) · F = DEPLOYED / REPLICA_OF (functional) ·
SP = DEPENDS_ON (set, projection DEP_NOW) · S = MEMBER_OF (set).
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1c]

INF = 9007199254740991
EPOCH = 1_791_000_000_000
FUTURE = 4_102_444_800_000
V1, V2, V3 = EPOCH + 100, EPOCH + 200, EPOCH + 300


def rw(world, pid):
    seq, pt = world.k.read("MATCH (r:Rewrite {id: $pid}) RETURN r.seq, r.pt", {"pid": pid})[0]
    return seq, pt


def guard(outcome):
    assert outcome.code == "REJECTED_GUARD", outcome
    return outcome.detail["guard"]


# ── Assert ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("rel", "s", "o", "proj", "extra"),
    [
        ("OWNED_BY", "svc:a", "team:t1", "OWNER_NOW", {}),
        ("DEPLOYED", "svc:a", "dep:d1", None, {}),
        ("REPLICA_OF", "svc:a", "svc:b", None, {}),
        ("DEPENDS_ON", "svc:a", "svc:b", "DEP_NOW", {"hard": True}),
        ("MEMBER_OF", "agent:ops-1", "team:t1", None, {}),
    ],
    ids=["FP-OWNED_BY", "F-DEPLOYED", "F-REPLICA_OF", "SP-DEPENDS_ON", "S-MEMBER_OF"],
)
def test_assert_creates_open_version(world, rel, s, o, proj, extra):
    out = world.assert_fact(rel, s, o, V1, pid="as1", **extra)
    assert out.code == "APPLIED", out
    seq, pt = rw(world, "as1")
    [v] = world.versions(rel)
    assert (v["s"], v["o"], v["fid"], v["prev_fid"], v["rw"]) == (s, o, "as1#0", None, "as1")
    assert (v["vf"], v["vt"], v["ts"], v["te"], v["tf"], v["tt"]) == (V1, INF, seq, INF, pt, INF)
    assert v["hard"] == extra.get("hard")
    if proj:
        assert world.projection(proj) == [[s, o, "as1#0", extra.get("hard")]]
    assert world.write_keys("as1") == [f"{rel}:{s}:*", f"{rel}:*:{o}", f"{rel}:*"]


def test_depends_on_external_service(world):
    assert world.assert_fact("DEPENDS_ON", "svc:a", "ext:x1", V1, hard=False, pid="dx").code == "APPLIED"
    assert world.projection("DEP_NOW") == [["svc:a", "ext:x1", "dx#0", False]]


def test_set_fact_allows_several_objects(world):
    assert world.assert_fact("DEPENDS_ON", "svc:a", "svc:b", V1, hard=True).code == "APPLIED"
    assert world.assert_fact("DEPENDS_ON", "svc:a", "svc:c", V2, hard=False).code == "APPLIED"
    assert world.assert_fact("MEMBER_OF", "agent:ops-1", "team:t1", V1).code == "APPLIED"
    assert world.assert_fact("MEMBER_OF", "agent:ops-1", "team:t2", V1).code == "APPLIED"
    assert len(world.projection("DEP_NOW")) == 2


# ── Supersede (functional) ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("rel", "s", "o1", "o2", "proj"),
    [("OWNED_BY", "svc:a", "team:t1", "team:t2", "OWNER_NOW"), ("REPLICA_OF", "svc:a", "svc:b", "svc:c", None)],
    ids=["FP-OWNED_BY", "F-REPLICA_OF"],
)
def test_supersede_closes_old_and_copies_it(world, rel, s, o1, o2, proj):
    assert world.assert_fact(rel, s, o1, V1, pid="s1").code == "APPLIED"
    out = world.assert_fact(rel, s, o2, V2, supersede=True, old_o=o1, pid="s2")
    assert out.code == "APPLIED", out
    seq2, pt2 = rw(world, "s2")
    seq1, pt1 = rw(world, "s1")
    by_fid = {v["fid"]: v for v in world.versions(rel)}
    assert set(by_fid) == {"s1#0", "s2#e", "s2#0"}
    old, copy, new = by_fid["s1#0"], by_fid["s2#e"], by_fid["s2#0"]
    assert (old["o"], old["vf"], old["vt"], old["ts"], old["te"], old["tf"], old["tt"]) == (o1, V1, INF, seq1, seq2, pt1,
                                                                                            pt2)
    assert (copy["o"], copy["vf"], copy["vt"], copy["prev_fid"], copy["ts"], copy["te"]) == (o1, V1, V2, "s1#0", seq2,
                                                                                            INF)
    assert (new["o"], new["vf"], new["vt"], new["ts"], new["te"], new["prev_fid"]) == (o2, V2, INF, seq2, INF, None)
    if proj:
        assert world.projection(proj) == [[s, o2, "s2#0", None]]
    assert world.write_keys("s2") == [f"{rel}:{s}:*", f"{rel}:*:{o2}", f"{rel}:*", f"{rel}:*:{o1}"]


def test_deployed_supersede(world):
    world.entity("Deploy", id="d3", service="a", version="1.1", verified=True, expected_health="ok")
    assert world.assert_fact("DEPLOYED", "svc:a", "dep:d1", V1).code == "APPLIED"
    assert world.assert_fact("DEPLOYED", "svc:a", "dep:d3", V2, supersede=True, old_o="dep:d1").code == "APPLIED"
    assert sorted((v["o"], v["vf"], v["vt"]) for v in world.current("DEPLOYED")) == [
        ("dep:d1", V1, V2), ("dep:d3", V2, INF),
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"old_o": "team:t3"},             # old_o is not the actual old object
        {"old_o": None},                  # missing old_o
        {"o": "team:t1"},                 # same object as before
        {"vf": V1},                       # new vf not after old.vf
        {"supersede": False},             # a functional fact is already open
    ],
    ids=["wrong-old_o", "missing-old_o", "same-object", "vf-not-after", "no-supersede-flag"],
)
def test_supersede_requires_actual_old_object(world, change):
    assert world.assert_fact("OWNED_BY", "svc:a", "team:t1", V1).code == "APPLIED"
    p = {"o": "team:t2", "vf": V2, "supersede": True, "old_o": "team:t1", **change}
    before = world.versions("OWNED_BY")
    out = world.assert_fact("OWNED_BY", "svc:a", p["o"], p["vf"], supersede=p["supersede"], old_o=p["old_o"])
    assert guard(out) == "cardinality_ok"
    assert world.versions("OWNED_BY") == before


def test_supersede_without_open_version_rejected(world):
    out = world.assert_fact("OWNED_BY", "svc:a", "team:t2", V2, supersede=True, old_o="team:t1")
    assert guard(out) == "cardinality_ok"


# ── End ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("rel", "s", "o", "proj", "extra"),
    [
        ("OWNED_BY", "svc:a", "team:t1", "OWNER_NOW", {}),
        ("DEPLOYED", "svc:a", "dep:d1", None, {}),
        ("DEPENDS_ON", "svc:a", "svc:b", "DEP_NOW", {"hard": True}),
        ("MEMBER_OF", "agent:ops-1", "team:t1", None, {}),
    ],
    ids=["FP", "F", "SP", "S"],
)
def test_end_creates_closed_current_copy(world, rel, s, o, proj, extra):
    assert world.assert_fact(rel, s, o, V1, pid="e1", **extra).code == "APPLIED"
    out = world.retract(rel, s, o, "e1#0", "end", V3, pid="e2")
    assert out.code == "APPLIED", out
    seq2, _ = rw(world, "e2")
    by_fid = {v["fid"]: v for v in world.versions(rel)}
    assert set(by_fid) == {"e1#0", "e2#0"}
    assert by_fid["e1#0"]["te"] == seq2
    end = by_fid["e2#0"]
    assert (end["vf"], end["vt"], end["prev_fid"], end["ts"], end["te"], end["o"]) == (V1, V3, "e1#0", seq2, INF, o)
    assert end["hard"] == extra.get("hard")
    if proj:
        assert world.projection(proj) == []
    assert world.write_keys("e2") == [f"{rel}:{s}:*", f"{rel}:*:{o}", f"{rel}:*"]


@pytest.mark.parametrize(("vt_end", "case"), [(FUTURE, "future"), (V1, "not-after-vf"), (None, "missing")])
def test_end_rejects_bad_vt_end(world, vt_end, case):
    world.assert_fact("OWNED_BY", "svc:a", "team:t1", V1, pid="e1")
    assert guard(world.retract("OWNED_BY", "svc:a", "team:t1", "e1#0", "end", vt_end)) == "mode_valid"
    assert world.projection("OWNER_NOW") == [["svc:a", "team:t1", "e1#0", None]]


def test_end_of_closed_version_rejected(world):
    world.assert_fact("OWNED_BY", "svc:a", "team:t1", V1, pid="e1")
    assert world.retract("OWNED_BY", "svc:a", "team:t1", "e1#0", "end", V2, pid="e2").code == "APPLIED"
    # e2#0 is current but already closed in valid time: it can be voided, not ended again
    assert guard(world.retract("OWNED_BY", "svc:a", "team:t1", "e2#0", "end", V3)) == "mode_valid"


# ── Void ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("rel", "s", "o", "proj", "extra"),
    [
        ("OWNED_BY", "svc:a", "team:t1", "OWNER_NOW", {}),
        ("REPLICA_OF", "svc:a", "svc:b", None, {}),
        ("DEPENDS_ON", "svc:a", "svc:b", "DEP_NOW", {"hard": False}),
        ("MEMBER_OF", "agent:ops-1", "team:t1", None, {}),
    ],
    ids=["FP", "F", "SP", "S"],
)
def test_void_closes_without_replacement(world, rel, s, o, proj, extra):
    world.assert_fact(rel, s, o, V1, pid="v1", **extra)
    out = world.retract(rel, s, o, "v1#0", "void", pid="v2")
    assert out.code == "APPLIED", out
    seq2, _ = rw(world, "v2")
    [v] = world.versions(rel)
    assert (v["fid"], v["te"]) == ("v1#0", seq2)
    assert world.current(rel) == []
    if proj:
        assert world.projection(proj) == []


def test_void_of_superseded_copy_keeps_projection(world):
    world.assert_fact("OWNED_BY", "svc:a", "team:t1", V1, pid="p1")
    world.assert_fact("OWNED_BY", "svc:a", "team:t2", V2, supersede=True, old_o="team:t1", pid="p2")
    assert world.retract("OWNED_BY", "svc:a", "team:t1", "p2#e", "void").code == "APPLIED"
    assert world.projection("OWNER_NOW") == [["svc:a", "team:t2", "p2#0", None]]


def test_retract_requires_current_version(world):
    world.assert_fact("OWNED_BY", "svc:a", "team:t1", V1, pid="r1")
    assert world.retract("OWNED_BY", "svc:a", "team:t1", "r1#0", "void").code == "APPLIED"
    assert guard(world.retract("OWNED_BY", "svc:a", "team:t1", "r1#0", "void")) == "version_current"
    assert guard(world.retract("OWNED_BY", "svc:a", "team:t2", "nope#0", "void")) == "version_current"


def test_retract_rejects_unknown_mode(world):
    world.assert_fact("OWNED_BY", "svc:a", "team:t1", V1, pid="m1")
    assert guard(world.retract("OWNED_BY", "svc:a", "team:t1", "m1#0", "erase")) == "mode_valid"


def test_retract_root_cause_copies_attributes(world, genesis):
    # ROOT_CAUSE is asserted by R10 (A06); seed one version directly to exercise RetractFact.ROOT_CAUSE.
    genesis.graph.query(
        "CREATE (i:Incident:Entity {uid: 'inc:i1', id: 'i1'}) WITH i MATCH (d:Deploy {id: 'd1'}) "
        "CREATE (i)-[:ROOT_CAUSE {fid: 'seed#0', vf: $vf, vt: $inf, tf: $vf, tt: $inf, ts: 1, te: $inf, rw: 'seed', "
        "by: 'agent:inv', confidence: 80}]->(d)", {"vf": V1, "inf": INF})
    assert world.retract("ROOT_CAUSE", "inc:i1", "dep:d1", "seed#0", "end", V2, pid="rc").code == "APPLIED"
    assert genesis.read("MATCH ()-[f:ROOT_CAUSE {fid: 'rc#0'}]->() RETURN f.by, f.confidence, f.vt") == [
        ["agent:inv", 80, V2]
    ]


# ── Guards ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("rel", "s", "o", "extra"),
    [
        ("OWNED_BY", "svc:a", "team:t1", {}), ("DEPLOYED", "svc:a", "dep:d1", {}),
        ("DEPENDS_ON", "svc:a", "svc:b", {"hard": True}), ("MEMBER_OF", "agent:ops-1", "team:t1", {}),
    ],
    ids=["FP", "F", "SP", "S"],
)
def test_vf_in_future_rejected(world, rel, s, o, extra):
    assert guard(world.assert_fact(rel, s, o, FUTURE, **extra)) == "vf_not_future"
    assert world.versions(rel) == []


@pytest.mark.parametrize(
    ("rel", "s", "o", "extra"),
    [
        ("OWNED_BY", "svc:a", "svc:b", {}),          # object is not a Team
        ("OWNED_BY", "svc:zz", "team:t1", {}),       # unknown subject
        ("DEPLOYED", "svc:a", "team:t1", {}),        # object is not a Deploy
        ("MEMBER_OF", "svc:a", "team:t1", {}),       # subject is not an Agent
        ("DEPENDS_ON", "team:t1", "svc:b", {"hard": True}),
    ],
    ids=["owned-by-service", "unknown-subject", "deployed-team", "member-of-service", "depends-on-from-team"],
)
def test_entities_must_exist_and_be_typed(world, rel, s, o, extra):
    assert guard(world.assert_fact(rel, s, o, V1, **extra)) == "entities_exist_and_typed"


def test_duplicate_open_set_fact_rejected(world):
    assert world.assert_fact("DEPENDS_ON", "svc:a", "svc:b", V1, hard=True).code == "APPLIED"
    assert guard(world.assert_fact("DEPENDS_ON", "svc:a", "svc:b", V2, hard=False)) == "cardinality_ok"
    assert world.assert_fact("MEMBER_OF", "agent:ops-1", "team:t1", V1).code == "APPLIED"
    assert guard(world.assert_fact("MEMBER_OF", "agent:ops-1", "team:t1", V2)) == "cardinality_ok"


@pytest.mark.parametrize(("rel", "extra"), [("DEPENDS_ON", {"hard": True}), ("REPLICA_OF", {})])
def test_self_dependency_rejected(world, rel, extra):
    assert guard(world.assert_fact(rel, "svc:a", "svc:a", V1, **extra)) == "extra"


def test_depends_on_needs_boolean_hard(world):
    assert guard(world.assert_fact("DEPENDS_ON", "svc:a", "svc:b", V1, hard="yes")) == "extra"
    assert guard(world.assert_fact("DEPENDS_ON", "svc:a", "svc:b", V1)) == "extra"


def test_deployed_requires_deploy_of_that_service(world):
    assert guard(world.assert_fact("DEPLOYED", "svc:a", "dep:d2", V1)) == "extra"  # d2 belongs to service b
    assert world.assert_fact("DEPLOYED", "svc:b", "dep:d2", V1).code == "APPLIED"


@pytest.mark.parametrize(
    ("rel", "s", "o", "extra"),
    [("OWNED_BY", "svc:a", "team:t1", {}), ("DEPENDS_ON", "svc:a", "svc:b", {"hard": True})],
    ids=["functional", "set"],
)
def test_assert_may_not_overlap_an_ended_version(world, rel, s, o, extra):
    """DECISIONS D-15: an assert after `end` must start at or after the ended version's vt."""
    world.assert_fact(rel, s, o, V1, pid="ov1", **extra)
    assert world.retract(rel, s, o, "ov1#0", "end", V3).code == "APPLIED"
    assert guard(world.assert_fact(rel, s, o, V2, **extra)) == "cardinality_ok"
    assert world.assert_fact(rel, s, o, V3, **extra).code == "APPLIED"


def test_fact_rules_deny_other_roles(world):
    assert world.assert_fact("OWNED_BY", "svc:a", "team:t1", V1, agent="triage-1").code == "CALLER_DENIED"
