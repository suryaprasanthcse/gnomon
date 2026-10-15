"""§15.7-§15.9 scoring, selection, footprint and evidence: integer arithmetic on hand-built scorer outputs."""

import pytest

from gnomon.executive import scoring

pytestmark = [pytest.mark.integration, pytest.mark.gate_tb]


def out(rows, teams=(), region=(), owners=()):
    """A scorer output with impacted rows (uid, w, parent, parent_fid)."""
    return {"version": 1, "region": sorted(region), "sources": [], "teams": sorted(teams),
            "impacted": sorted([u, "down", 0 if p is None else 1, w, p, f] for u, w, p, f in rows),
            "owner_edges": sorted(list(o) for o in owners)}


def test_constants_are_the_appendix_c_values():
    assert (scoring.W, scoring.TAU, scoring.LAMBDA_N, scoring.LAMBDA_D, scoring.MARGIN, scoring.EVIDENCE_N,
            scoring.K_MAX, scoring.HOPS) == (2520, 2520, 3, 2, 1260, 3, 8, 4)


def test_scoring_integer_formulas():
    base = out([("svc:u1", 100, None, None), ("svc:u2", 50, None, None)], teams=["team:t1"])
    branch = out([("svc:u1", 30, None, None), ("svc:u3", 40, None, None)], teams=["team:t1", "team:t2"])
    got = scoring.score(base, branch)
    # benefit = (100-30) + (50-0) = 120; harm = (40-0) + TAU*1 = 2560; score_num = 120*2 - 3*2560
    assert got == {"benefit": 120, "harm": 2560, "teams_new": 1, "score_num": -7440}
    assert all(isinstance(v, int) for v in got.values())


def test_a_pure_improvement_has_no_harm():
    base = out([("svc:u1", 100, None, None)], teams=["team:t1"])
    branch = out([], teams=[])
    assert scoring.score(base, branch) == {"benefit": 100, "harm": 0, "teams_new": 0, "score_num": 200}


def test_the_base_scored_against_itself_is_zero():
    base = out([("svc:u1", 100, None, None)], teams=["team:t1"])
    assert scoring.score(base, base) == {"benefit": 0, "harm": 0, "teams_new": 0, "score_num": 0}


def cand(k, score_num, sha, outcome="scored"):
    return {"k": k, "outcome": outcome, "score_num": score_num if outcome == "scored" else None, "params_sha": sha}


def test_selection_is_the_highest_score_and_ties_go_to_the_lowest_params_sha():
    cands = [cand(0, 0, "00"), cand(1, 9000, "bb"), cand(2, 9000, "aa"), cand(3, 5000, "cc")]
    assert scoring.select(cands) == 2
    assert scoring.select([cand(0, 0, "00"), cand(1, 9000, "bb")]) == 1


def test_pruned_errored_and_vetoed_candidates_never_win():
    cands = [cand(0, 0, "00"), cand(1, None, "aa", "guard_pruned"), cand(2, None, "bb", "error"),
             {**cand(3, 99999, "cc"), "outcome": "vetoed"}]
    assert scoring.select(cands) == 0


def test_margin_falls_back_to_escalate():
    just_below = scoring.MARGIN * scoring.LAMBDA_D - 1
    assert scoring.select([cand(0, 0, "00"), cand(1, just_below, "aa")]) == 0
    assert scoring.select([cand(0, 0, "00"), cand(1, just_below + 1, "aa")]) == 1  # the margin itself is enough


def test_escalate_always_wins_when_nothing_else_is_scored():
    assert scoring.select([cand(0, 0, "00")]) == 0


def test_footprint_keys_of_the_region():
    base = out([], region=["svc:a", "svc:b"])
    keys = scoring.footprint(base, "i1")
    assert keys == sorted({
        "Incident:i1:state", "ROOT_CAUSE:inc:i1:*",
        "Service:a:state", "DEPENDS_ON:svc:a:*", "DEPENDS_ON:*:svc:a", "OWNED_BY:svc:a:*", "DEPLOYED:svc:a:*",
        "REPLICA_OF:*:svc:a",
        "Service:b:state", "DEPENDS_ON:svc:b:*", "DEPENDS_ON:*:svc:b", "OWNED_BY:svc:b:*", "DEPLOYED:svc:b:*",
        "REPLICA_OF:*:svc:b"})


def test_params_sha_is_the_candidate_identity():
    a = scoring.params_sha("RollbackDeploy", {"svc": "c", "incident": "i1"})
    assert a == scoring.params_sha("RollbackDeploy", {"incident": "i1", "svc": "c"})
    assert a != scoring.params_sha("IsolateService", {"svc": "c", "incident": "i1"})
    assert len(a) == 64


def test_evidence_paths_and_cites():
    base = out([("svc:c", 10, None, None), ("svc:b", 8, "svc:c", "f-bc"), ("svc:a", 6, "svc:b", "f-ab"),
                ("svc:f", 4, "svc:c", "f-fc")],
               teams=["team:t1", "team:t2"],
               owners=[("svc:a", "team:t1", "o-a"), ("svc:b", "team:t2", "o-b"), ("svc:c", "team:t2", "o-c"),
                       ("svc:f", "team:t2", "o-f")])
    winner = out([("svc:c", 10, None, None)], teams=["team:t2"], owners=[("svc:c", "team:t2", "o-c")])
    evidence, cites = scoring.evidence(base, winner)
    rescued = [e for e in evidence if e["role"] == "rescued"]
    # rescued by difference: b 8, a 6, f 4 (EVIDENCE_N = 3); c is unchanged
    assert [e["path"][-1] for e in rescued] == ["svc:b", "svc:a", "svc:f"]
    assert rescued[0]["path"] == ["svc:c", "svc:b"] and rescued[1]["path"] == ["svc:c", "svc:b", "svc:a"]
    assert rescued[0]["cites"] == [{"s": "svc:b", "fid": "f-bc"}]
    assert rescued[1]["cites"] == [{"s": "svc:b", "fid": "f-bc"}, {"s": "svc:a", "fid": "f-ab"}]
    owners = [e for e in evidence if e["role"] == "owners"]
    assert [e["path"] for e in owners] == [["svc:a", "team:t1"], ["svc:b", "team:t2"], ["svc:c", "team:t2"],
                                              ["svc:f", "team:t2"]]
    assert owners[0]["cites"] == [{"s": "svc:a", "fid": "o-a"}]
    assert [e["ord"] for e in evidence] == list(range(len(evidence)))
    assert cites == sorted(cites, key=lambda c: (c["s"], c["fid"])) and len(cites) == len({(c["s"], c["fid"]) for c in cites})
    assert {(c["s"], c["fid"]) for e in evidence for c in e["cites"]} <= {(c["s"], c["fid"]) for c in cites}


def test_harmed_evidence_walks_the_winner_branch():
    base = out([("svc:c", 10, None, None)], teams=["team:t1"], owners=[("svc:c", "team:t1", "o-c")])
    winner = out([("svc:c", 10, None, None), ("svc:g", 7, "svc:c", "f-gc")], teams=["team:t1", "team:t9"],
                 owners=[("svc:c", "team:t1", "o-c"), ("svc:g", "team:t9", "o-g")])
    evidence, _ = scoring.evidence(base, winner)
    harmed = [e for e in evidence if e["role"] == "harmed"]
    assert [e["path"] for e in harmed] == [["svc:c", "svc:g"]] and harmed[0]["cites"] == [{"s": "svc:g", "fid": "f-gc"}]
