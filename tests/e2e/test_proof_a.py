"""Proof A · Counterfactual action (master §26): every action is tested in its own copy of the world first.

Three branches side by side (one guard-pruned), integer scores, the live Head.hash unchanged across the simulations,
and a commit with its Evidence. The numbers are the hand-computed ones of tests/support/exec_world.py:
B = c 10080 + b 7560 + a 5040 + f 5040 = 27720; failing over to c-r1 clears all of it (benefit 27720, harm 0,
score_num 27720 * 2 - 3 * 0 = 55440); isolating c only clears the soft dependent f (5040 * 2 = 10080).
"""

import pytest

from gnomon.audit.runner import audit

pytestmark = [pytest.mark.e2e, pytest.mark.gate_tb]


def test_three_futures_scored_in_their_own_copies_then_one_commit(xw, db, redis_raw):
    ex = xw.executive()
    live = xw.k.head()
    dry = ex.simulate("i1")
    assert xw.k.head() == live and xw.sim_graphs() == []  # simulations leave live untouched and retire their graphs
    scored = {(c["rule"], c["params"].get("replica")): c for c in dry.candidates}
    assert (scored["IsolateService", None]["benefit"], scored["IsolateService", None]["score_num"]) == (5040, 10080)
    assert (scored["FailoverService", "c-r1"]["benefit"], scored["FailoverService", "c-r1"]["score_num"]) == (
        27720, 55440)
    assert scored["FailoverService", "c-r2"]["outcome"] == "guard_pruned"
    assert scored["EscalateIncident", None]["score_num"] == 0

    res = ex.decide("i1", [], "fail over", "proof-a")
    assert res.state == "DONE" and res.outcome.code == "APPLIED" and res.winner["params"]["replica"] == "c-r1"
    assert [c["score_num"] for c in res.candidates] == [c["score_num"] for c in dry.candidates]  # same futures, same integers
    assert xw.k.read("MATCH (:Decision {id: $d})-[:CONSIDERED]->(c:Candidate) RETURN count(c)", {"d": res.did}) == [[4]]
    assert xw.k.read("MATCH (:Decision {id: $d})-[:JUSTIFIED_BY]->(e:Evidence) RETURN count(e) > 0", {"d": res.did}) == [
        [True]]
    assert audit(db, redis_raw, xw.k.graph.name, ["K1", "K4", "K5", "K7"]).ok
    assert xw.sim_graphs() == []
