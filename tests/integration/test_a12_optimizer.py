"""A12 · gate TC: the self-optimizing engine (master §16) and audit K8.

Pure parts first (shape normalization, plan parsing, detection, cost model, candidate filter), then the engine on a
real FalkorDB over a seeded ownership graph: telemetry, sampling, shadow trials, M1, the reconciler, M2, verification,
K7 for index decisions, K8 and the mutations MU24-MU25, the state-hash and ledger-determinism audit, and proof C.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from tests.support.ir_world import ir, opened  # noqa: F401  (fixtures the golden world builds on)
from tests.support.opt_world import (  # noqa: F401
    INF, OPT, Q5_FILE, REC, RecordedMeasurer, golden_opt, make_ow, ow,
)

pytestmark = [pytest.mark.integration, pytest.mark.gate_tc]

ROOT = Path(__file__).resolve().parents[2]
PLANS = ROOT / "tests" / "fixtures" / "plans"
Q5_VF = "edge:OWNED_BY:vf"


def cfg(**over):
    from gnomon.optimizer.config import OptConfig

    return OptConfig(**over)


def fixture_plan(name):
    from gnomon.optimizer import plan

    return plan.parse((PLANS / name).read_text(encoding="utf-8").splitlines())


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ───────────────────────────────────────────────────────────── pure: configuration, shapes, plans

def test_defaults_are_the_appendix_c_values():
    c = cfg()
    assert (c.period_ms, c.window_ms, c.top_n, c.param_sets, c.warmup, c.runs) == (900_000, 3_600_000, 10, 3, 3, 15)
    assert (c.theta_num, c.theta_den, c.kappa, c.amort_windows, c.mu_us_per_mb, c.margin_us) == (1, 10, 3, 24, 50, 5000)
    assert (c.index_budget, c.cooldown_ms, c.drop_windows, c.ddl_timeout_ms) == (24, 3_600_000, 6, 300_000)


def test_shape_normalization_lifts_literals_and_equal_shapes_share_a_sha():
    from gnomon.optimizer import shapes

    a = shapes.normalize("MATCH (n:Svc) WHERE n.tier = 'gold' AND n.crit >= 7 RETURN n.id")
    b = shapes.normalize("match (n:Svc)\n  where n.tier = 'silver'  and n.crit >= 9  return n.id  // a comment")
    assert a.sha == b.sha and a.text == b.text
    assert sha(a.text) == a.sha
    assert "gold" not in a.text and "7" not in a.text
    assert sorted(map(str, a.params.values())) == ["7", "gold"]
    assert a.params != b.params
    other = shapes.normalize("MATCH (n:Svc) WHERE n.tier <> 'gold' AND n.crit >= 7 RETURN n.id")
    label = shapes.normalize("MATCH (n:Team) WHERE n.tier = 'gold' AND n.crit >= 7 RETURN n.id")
    assert len({a.sha, other.sha, label.sha}) == 3


def test_shape_normalization_keeps_parameters_and_variable_length_bounds():
    from gnomon.optimizer import shapes

    s = shapes.normalize("MATCH (a)-[:DEP_NOW*0..4]->(b) WHERE b.v = $x AND b.w = 5 RETURN b", {"x": 1})
    assert "*0..4" in s.text and "$x" in s.text
    assert s.params["x"] == 1 and 5 in s.params.values() and len(s.params) == 2


# the plan canonical form pinned here: one line per operation, "<depth>|<op>|<argument or empty>", aliases a0, a1, ...
Q5_EXPLAIN_CANONICAL = "\n".join([
    "0|Sort|", "1|Project|", "2|Cartesian Product|", "3|Node By Label Scan|(a0:Head)", "3|Filter|", "4|Filter|",
    "5|Edge By Index Scan|(a1)-[a2:OWNED_BY]->(a3:Team)", "6|Node By Label Scan|(a1:Service)"])


def test_plan_parser_reads_the_a01_fixtures_into_a_tree_and_a_plan_sha():
    from gnomon.optimizer import plan

    p = fixture_plan("explain_q5_edge_index.txt")
    assert p.rows() == [
        (0, "Sort", None), (1, "Project", None), (2, "Cartesian Product", None),
        (3, "Node By Label Scan", "(h:Head)"), (3, "Filter", None), (4, "Filter", None),
        (5, "Edge By Index Scan", "(s)-[f:OWNED_BY]->(t:Team)"), (6, "Node By Label Scan", "(s:Service)")]
    assert p.canonical() == Q5_EXPLAIN_CANONICAL
    assert p.sha == sha(Q5_EXPLAIN_CANONICAL)
    t = fixture_plan("explain_traverse_filter.txt")
    assert [(r[1], r[2]) for r in t.rows()] == [("Sort", None), ("Project", None), ("Conditional Traverse", "(a)->(b:B)"),
                                                ("Filter", None), ("Node By Label Scan", "(a:A)")]
    assert plan.parse(["Sort", "    Project"]).sha != t.sha


def test_profile_lines_carry_records_and_time_but_not_into_the_sha():
    from gnomon.optimizer import plan

    p = plan.parse(["Project | Records produced: 3, Execution time: 0.5 ms",
                    "    Node By Label Scan | (s:Service) | Records produced: 9, Execution time: 1.25 ms"])
    assert [(n.op, n.arg, n.records) for n in p.walk()] == [("Project", None, 3), ("Node By Label Scan", "(s:Service)", 9)]
    assert p.root.children[0].ms == 1.25
    explain = plan.parse(["Project", "    Node By Label Scan | (s:Service)"])
    assert p.sha == explain.sha
    assert plan.parse(["X | (zz:L)"]).sha == plan.parse(["X | (a:L)"]).sha != plan.parse(["X | (a:M)"]).sha


def test_plans_report_the_index_scans_they_use():
    from gnomon.optimizer import plan

    p = fixture_plan("explain_q5_edge_index.txt")
    assert p.index_scans() == [("edge", "OWNED_BY")]
    assert plan.parse(["Node By Index Scan | (n:Svc)"]).index_scans() == [("node", "Svc")]
    assert plan.parse(["Node By Label Scan | (n:Svc)"]).index_scans() == []


# ───────────────────────────────────────────────────────────── pure: detection D1-D4

def keys(cands):
    return [(c.rule, c.key) for c in cands]


def test_d1_filter_above_a_label_scan_gives_a_node_index():
    from gnomon.optimizer import detect

    p = fixture_plan("profile_d1_label_scan_filter.txt")
    got = detect.candidates("MATCH (s:Service) WHERE s.tier = $tier RETURN s.id", p, cfg())
    assert [(c.rule, c.key, c.entity, c.label, c.prop) for c in got] == [("D1", "node:Service:tier", "node", "Service", "tier")]
    for text in ("MATCH (s:Service) WHERE s.crit >= $c RETURN s.id", "MATCH (s:Service) WHERE s.tier IN $t RETURN s.id",
                 "MATCH (s:Service) WHERE s.crit < 3 RETURN s.id"):
        assert keys(detect.candidates(text, p, cfg()))[0][0] == "D1"


def test_d1_needs_selectivity_below_theta_and_a_supported_predicate():
    from gnomon.optimizer import detect

    flat = fixture_plan("profile_d1_unselective.txt")
    assert detect.candidates("MATCH (s:Service) WHERE s.tier = $tier RETURN s.id", flat, cfg()) == []
    p = fixture_plan("profile_d1_label_scan_filter.txt")
    for text in ("MATCH (s:Service) WHERE toLower(s.tier) = $t RETURN s.id",
                 "MATCH (s:Service) WHERE s.tier = $a OR s.crit = $b RETURN s.id",
                 "MATCH (s:Service) WHERE NOT s.tier = $a RETURN s.id"):
        assert detect.candidates(text, p, cfg()) == []
    assert keys(detect.candidates("MATCH (s:Service) WHERE s.tier = $a OR s.tier = $b RETURN s.id", p, cfg())) == [
        ("D1", "node:Service:tier")]


def test_d2_filter_above_a_traverse_that_binds_the_edge():
    from gnomon.optimizer import detect

    p = fixture_plan("profile_d2_traverse_filter.txt")
    text = "MATCH (s:Service)-[f:OWNED_BY]->(t:Team) WHERE f.vf >= $a AND s.tier = $b RETURN s.id"
    assert keys(detect.candidates(text, p, cfg())) == [("D2", "edge:OWNED_BY:vf")]


def test_d3_is_the_q5_change_feed_case_and_a_negated_te_filter_gives_no_candidate():
    from gnomon.optimizer import detect

    p = fixture_plan("profile_q5_traverse_filter.txt")
    got = detect.candidates(Q5_FILE.read_text(encoding="utf-8"), p, cfg())
    assert keys(got) == [("D3", Q5_VF)]
    assert (got[0].entity, got[0].label, got[0].prop) == ("edge", "OWNED_BY", "vf")


def test_d4_nominates_an_optimizer_index_unused_for_drop_windows_consecutive_windows():
    from gnomon.optimizer import detect, plan

    idx = [{"key": "node:Service:tier", "entity": "node", "label": "Service", "prop": "tier"}]
    unused = plan.parse(["Project", "    Node By Label Scan | (s:Service)"])
    used = plan.parse(["Project", "    Node By Index Scan | (s:Service)"])
    c, state = cfg(), {}
    for _ in range(c.drop_windows - 1):
        nominees, state = detect.drop_candidates(idx, [unused], state, c)
        assert nominees == []
    nominees, state = detect.drop_candidates(idx, [unused], state, c)
    assert [(n.rule, n.key, n.op) for n in nominees] == [("D4", "node:Service:tier", "drop")]
    nominees, state = detect.drop_candidates(idx, [used], state, c)
    assert nominees == [] and state.get("node:Service:tier", 0) == 0


def test_the_candidate_filter_removes_what_exists_is_over_budget_cooling_down_or_off_limits():
    from gnomon.optimizer import detect

    c = cfg(index_budget=2, cooldown_ms=1000)
    mk = lambda key, op="create": detect.Candidate(rule="D1", key=key, entity=key.split(":")[0], label=key.split(":")[1],
                                                   prop=key.split(":")[2], op=op)
    entries = [
        {"key": "edge:OWNED_BY:fid", "state": "operational", "origin": "genesis"},
        {"key": "node:A:x", "state": "requested", "origin": "optimizer"},
        {"key": "node:B:y", "state": "retired", "origin": "optimizer", "settled_pt": 9_500},
        {"key": "node:C:z", "state": "failed", "origin": "optimizer", "settled_pt": 1_000}]
    cands = [mk("edge:OWNED_BY:fid"), mk("node:A:x"), mk("node:B:y"), mk("node:C:z")]
    kept, dropped = detect.filter_candidates(cands, entries, n_opt=0, now=10_000, config=c, constraint_indexes=set())
    assert [k.key for k in kept] == ["node:C:z"]
    assert dict((d.key, r) for d, r in dropped) == {"edge:OWNED_BY:fid": "exists", "node:A:x": "exists",
                                                   "node:B:y": "cooldown"}
    kept, dropped = detect.filter_candidates([mk("node:D:w")], entries, n_opt=2, now=10_000, config=c,
                                             constraint_indexes=set())
    assert kept == [] and dropped[0][1] == "budget"
    drops = [mk("edge:OWNED_BY:fid", "drop"), mk("node:Q:id", "drop")]
    entries.append({"key": "node:Q:id", "state": "operational", "origin": "optimizer", "settled_pt": 0})
    kept, dropped = detect.filter_candidates(drops, entries, n_opt=9, now=10_000, config=c,
                                             constraint_indexes={"node:Q:id"})
    assert kept == [] and {r for _, r in dropped} == {"off_limits"}


# ───────────────────────────────────────────────────────────── pure: cost model

def test_median_and_mad_use_integer_arithmetic():
    from gnomon.optimizer import cost

    assert cost.median([5, 1, 3]) == 3 and cost.median([1, 2, 3, 10]) == 2
    assert cost.mad([1, 2, 3, 4, 100]) == 1
    assert cost.significant(1000, 5, 200, 5, 3) and not cost.significant(1000, 300, 800, 100, 3)


def test_cost_model_from_recorded_samples():
    from gnomon.optimizer import cost

    c = cfg()
    base = {"qA": [1000, 1010, 990, 1005, 995], "qB": [100, 100, 100]}
    cand = {"qA": [200, 210, 190, 205, 195], "qB": [99, 101, 100]}
    score = cost.score(base, cand, {"qA": 10, "qB": 4}, {"w": [50, 52, 48]}, {"w": [60, 62, 58]}, {"w": 20},
                       48_000, c)
    # qA: |1000-200| > 3*(5+5) so 10*800 = 8000; qB median unchanged; write 20*10 = 200; build 48000//24 = 2000
    assert score.as_dict() == {"gain_us": 8000, "write_us": 200, "build_amort_us": 2000, "mem_us": 0, "net_us": 5800}
    assert cost.accept(True, score.net_us, c.margin_us) and not cost.accept(False, score.net_us, c.margin_us)
    assert not cost.accept(True, c.margin_us - 1, c.margin_us)
    assert all(isinstance(v, int) for v in score.as_dict().values())
    assert cost.digest({"b": 1, "a": [2]}) == sha('{"a":[2],"b":1}')


def test_drop_scoring_reverses_the_signs():
    from gnomon.optimizer import cost

    c = cfg()
    with_ix = {"q": [200, 205, 195]}
    without = {"q": [1000, 1005, 995]}
    s = cost.score_drop(with_ix, without, {"q": 1}, {"w": [60, 62, 58]}, {"w": [50, 52, 48]}, {"w": 20}, c)
    assert s.net_us == 200 - 800  # write saving 20*10, read loss 1*800
    quiet = cost.score_drop(with_ix, with_ix, {"q": 1}, {"w": [60, 62, 58]}, {"w": [50, 52, 48]}, {"w": 20}, c)
    assert quiet.net_us == 200


# ───────────────────────────────────────────────────────────── telemetry and sampling

def test_the_query_service_appends_workload_telemetry(ow):
    from gnomon.kernel.service import wire_text
    from gnomon.optimizer import shapes

    ow.reads(3)
    rows = ow.client.xrange(f"{ow.key}:wl")
    assert len(rows) == 3
    f = rows[0][1]
    assert set(f) == {"shape_sha", "cypher", "params", "internal_ms", "rows", "pt", "caller"}
    assert f["shape_sha"] == shapes.normalize(wire_text(Q5_FILE.read_text(encoding="utf-8"))).sha
    assert f["caller"] == "inv-1" and float(f["internal_ms"]) > 0 and int(f["rows"]) >= 1
    assert {"T1", "T2", "INF"} <= set(json.loads(f["params"]))
    assert int(f["pt"]) <= ow.now()


def test_workload_sampling_takes_the_top_shapes_by_total_time_in_the_window(redis_raw, tenant):
    from gnomon.optimizer import workload

    now = [10_000_000]
    tel = workload.Telemetry(redis_raw, tenant, clock=lambda: now[0])
    A = "MATCH (s:Service) WHERE s.tier = '%s' RETURN s.id"
    B = "MATCH (t:Team) WHERE t.id = 'x' RETURN t.id"
    now[0] = 1_000_000
    tel.record("MATCH (o:Old) WHERE o.v = 1 RETURN o", {}, 900.0, 1, "inv-1")  # outside the window
    for i, tier in enumerate(["g1", "g2", "g3", "g4", "g4"]):
        now[0] = 9_500_000 + i
        tel.record(A % tier, {}, 5.0, 1, "inv-1")
    now[0] = 9_600_000
    tel.record(B, {}, 40.0, 1, "inv-1")
    c = cfg(window_ms=1_000_000, top_n=2, param_sets=3)
    w = workload.sample(redis_raw, tenant, 10_000_000, c)
    assert [s.total_us for s in w.shapes] == [40_000, 25_000]
    assert [s.freq for s in w.shapes] == [1, 5]
    a = w.shapes[1]
    assert len(a.param_sets) == 3 and a.param_sets[0] == {"_l0": "g4"} and a.param_sets[1] == {"_l0": "g3"}
    assert workload.sample(redis_raw, tenant, 10_000_000, c).digest == w.digest
    assert [s.freq for s in workload.sample(redis_raw, tenant, 10_000_000, cfg(window_ms=1_000_000, top_n=1)).shapes] == [1]
    assert workload.sample(redis_raw, tenant, 99_000_000, c).shapes == []


# ───────────────────────────────────────────────────────────── the Q5 case, end to end

def test_q5_slow_shape_is_a_traverse_and_a_filter_until_the_edge_index_exists(ow):
    ow.seed_q5(1)
    text = Q5_FILE.read_text(encoding="utf-8")
    before = "\n".join(ow.client.execute_command("GRAPH.EXPLAIN", ow.key, text))
    assert "Conditional Traverse" in before and "Edge By Index Scan" not in before


def test_q5_seeded_slow_yields_edge_owned_by_vf(ow):
    ow.reads(30)
    head = ow.k.head()["seq"]
    run = ow.optimizer().run()
    assert run.state == "AWAIT_SETTLE", run.trace
    scored = [c["key"] for c in run.candidates if c.get("outcome") == "scored" and c["k"] > 0]
    assert scored == [Q5_VF]
    assert run.winner["key"] == Q5_VF and run.winner["score_num"] >= ow.cfg.margin_us
    assert run.outcome.code == "APPLIED" and ow.k.head()["seq"] == head + 1
    entry = ow.entry(Q5_VF)
    assert entry["state"] == "requested" and entry["origin"] == "optimizer" and entry["op"] == "create"
    assert Q5_VF not in ow.physical()  # the optimizer never runs DDL on live


def test_trials_only_on_scratch(ow):
    ow.reads(20)
    before = ow.physical()
    seen = []
    opt = ow.optimizer(observe=lambda state, run: seen.append((state, ow.physical() == before, ow.scratch())))
    run = opt.run()
    assert all(same for state, same, _ in seen)
    assert any(state == "TRIAL" and scratch for state, _, scratch in seen)
    assert any(g.endswith(":base") for g in max((s for *_, s in seen), key=len))
    assert run.state == "AWAIT_SETTLE" and ow.physical() == before
    ow.reconciler().tick()
    opt.resume(run)
    assert ow.scratch() == [] and Q5_VF in ow.physical()


def test_plan_change_required_a_candidate_whose_plan_does_not_change_is_discarded(ow):
    from gnomon.optimizer import detect

    ow.reads(20)
    extra = detect.Candidate(rule="X", key="edge:OWNED_BY:vt", entity="edge", label="OWNED_BY", prop="vt")
    run = ow.optimizer().run(extra_candidates=[extra])
    byk = {c.get("key"): c for c in run.candidates}
    assert byk["edge:OWNED_BY:vt"]["outcome"] == "discarded" and byk["edge:OWNED_BY:vt"]["reason"] == "plan_unchanged"
    assert byk["edge:OWNED_BY:vt"].get("score_num") is None
    assert run.winner["key"] == Q5_VF


def test_the_write_mix_is_synthesized_and_measured_on_both_shadows(ow):
    ow.reassign_owners(5)
    ow.reads(60)  # enough reads that the measured gain clears the margin even when the machine is busy
    run = ow.optimizer().run()
    env = run.envelope
    assert env["workload"]["write_mix"] == {"AssertFact.OWNED_BY": 5}
    meas = env["measurements"]
    assert len(meas["base"]["write"]["AssertFact.OWNED_BY"]) == 5
    assert len(meas["candidates"]["1"]["write"]["AssertFact.OWNED_BY"]) == 5
    assert meas["candidates"]["1"]["build_us"] >= 0


def test_the_decision_envelope_of_kind_index(ow):
    from gnomon.executive import envelope
    from gnomon.optimizer import cost, scorer

    ow.reads(20)
    run = ow.optimizer().run()
    env = run.envelope
    assert envelope.verify(env) and env["kind"] == "index" and env["proposer"] == "optimizer"
    assert env["scorer"] == "Opt.queryCost" == scorer.NAME and env["scorer_sha"] == scorer.sha()
    assert (env["lambda_n"], env["lambda_d"], env["margin"]) == (1, 1, ow.cfg.margin_us)
    k0, k1 = env["candidates"][0], env["candidates"][1]
    assert (k0["k"], k0["score_num"], k0["won"]) == (0, 0, False)
    assert k1["won"] and k1["params"] == {"op": "create", "entity": "edge", "label": "OWNED_BY", "prop": "vf", "key": Q5_VF}
    assert k1["plan_before"] != k1["plan_after"]
    assert env["workload_digest"] == cost.digest(env["workload"])
    assert k1["measure_digest"] == cost.digest(env["measurements"]["candidates"]["1"])
    roles = sorted({e["role"] for e in env["evidence"]})
    assert roles == ["plan_after", "plan_before"] and all(e["algo"] == "EXPLAIN" and e["cites"] == [] for e in env["evidence"])
    assert any("Edge By Index Scan" in e["path"] for e in env["evidence"] if e["role"] == "plan_after")
    rw = ow.k.read("MATCH (r:Rewrite {id: $p}) RETURN r.view_keys, r.write_keys", {"p": envelope.pid_of(env["did"])})[0]
    assert Q5_VF.join(["Index:", ""]) in rw[0] and "Index:*" in rw[0]
    assert rw[1] == [f"Index:{Q5_VF}", "Index:*"]
    assert ow.k.read("MATCH (x:Decision {id: $d}) RETURN x.kind", {"d": env["did"]}) == [["index"]]


# ───────────────────────────────────────────────────────────── the run state machine

def test_run_state_machine_paths(ow):
    empty = ow.optimizer().run()
    assert empty.trace == ["IDLE", "SAMPLE", "IDLE"]
    ow.seed_shape("MATCH (s:Service {id: $id}) RETURN s.id", {"id": "s5"}, 5, 0.2)
    quiet = ow.optimizer().run()
    assert quiet.trace == ["IDLE", "SAMPLE", "BASELINE", "DETECT", "RETIRE_CHECK", "CLEANUP", "COOLDOWN"]
    ow.reads(20)
    opt = ow.optimizer()
    run = opt.run()
    assert run.trace == ["IDLE", "SAMPLE", "BASELINE", "DETECT", "GENERATE", "TRIAL", "SCORE", "DECIDE", "COMMIT",
                         "AWAIT_SETTLE"]
    assert opt.resume(run).state == "AWAIT_SETTLE"  # the reconciler has not run yet
    ow.reconciler().tick()
    opt.resume(run)
    assert run.trace[-3:] == ["VERIFY", "CLEANUP", "COOLDOWN"] and run.verify["ok"] is True


def test_no_winner_goes_to_retire_check_and_a_stale_commit_goes_to_cleanup(ow):
    ow.seed_q5(20)
    losing = ow.optimizer(measurer=RecordedMeasurer(base=1000, cand=990))
    run = losing.run()
    assert run.trace[-5:] == ["SCORE", "DECIDE", "RETIRE_CHECK", "CLEANUP", "COOLDOWN"] and run.winner is None
    assert ow.k.read("MATCH (x:Decision) WHERE x.kind = 'index' RETURN count(x)") == [[0]]

    def interfere(run):
        ow.logical_index("node", "Service", "tier")

    stale = ow.optimizer(before_commit=interfere)
    run = stale.run()
    assert run.outcome.code == "REJECTED_STALE"
    assert run.trace[-4:] == ["DECIDE", "COMMIT", "CLEANUP", "COOLDOWN"] and ow.scratch() == []


def test_the_cooldown_state_holds_the_next_run_back(ow):
    ow.seed_shape("MATCH (s:Service {id: $id}) RETURN s.id", {"id": "s5"}, 5, 0.2)
    opt = ow.optimizer()
    assert opt.run().trace[-1] == "COOLDOWN"
    early = opt.run()
    assert early.trace == ["COOLDOWN"] and ow.scratch() == []
    ow.ir.clock += ow.cfg.cooldown_ms
    assert opt.run().trace[:3] == ["IDLE", "SAMPLE", "BASELINE"]


def test_a_failed_settle_skips_verification(ow):
    from gnomon.ops.reconciler import Reconciler  # noqa: F401
    import redis

    class Broken:
        def run(self, graph, statement):
            raise redis.ResponseError("injected DDL failure")

    ow.reads(20)
    opt = ow.optimizer()
    run = opt.run()
    ow.reconciler(ddl=Broken()).tick()
    opt.resume(run)
    assert ow.entry(Q5_VF)["state"] == "failed"
    assert run.trace[-2:] == ["CLEANUP", "COOLDOWN"] and "VERIFY" not in run.trace
    assert ow.scratch() == []


def test_verify_after_settle_explain_on_live_shows_the_edge_index_scan(ow):
    ow.reads(20)
    opt = ow.optimizer()
    run = opt.run()
    ow.reconciler().tick()
    opt.resume(run)
    live = "\n".join(ow.client.execute_command("GRAPH.EXPLAIN", ow.key, Q5_FILE.read_text(encoding="utf-8")))
    assert "Edge By Index Scan" in live and "OWNED_BY" in live
    assert run.verify["ok"] and run.verify["shapes"] and all(s["index_used"] for s in run.verify["shapes"])


# ───────────────────────────────────────────────────────────── M1 / M2

def test_m1_guards(ow):
    from gnomon.executive import envelope  # noqa: F401

    def guard(out):
        assert out.code == "REJECTED_GUARD", out
        return out.detail["guard"]

    assert guard(ow.m1("rename", "edge", "OWNED_BY", "vt")) == "G4:op_valid"
    assert guard(ow.m1("create", "vertex", "OWNED_BY", "vt")) == "G5:entity_valid"
    bad_key = ow.envelope("create", "edge", "OWNED_BY", "vt")
    p = dict(bad_key["candidates"][1]["params"], key="edge:OWNED_BY:other")
    out = ow.k.submit("IndexChange", envelope.params_for(p, bad_key), pid=envelope.pid_of(bad_key["did"]), agent=OPT,
                      read_seq=bad_key["read_seq"], view_keys=["Index:edge:OWNED_BY:vt", "Index:*"],
                      attach=envelope.attach_of(bad_key), mode="replay", rec_pt=ow.ir.clock + 10, rec_lc=0)
    assert guard(out) == "G6:key_matches"
    # genesis-origin drop refused; creating an index that exists refused; dropping what does not exist refused
    assert guard(ow.m1("drop", "edge", "OWNED_BY", "fid")) == "G7:state_allows_op"
    assert guard(ow.m1("create", "edge", "OWNED_BY", "fid")) == "G7:state_allows_op"
    assert guard(ow.m1("drop", "node", "Service", "nothing")) == "G7:state_allows_op"
    # one in flight at a time
    assert ow.m1("create", "edge", "OWNED_BY", "vt").code == "APPLIED"
    assert guard(ow.m1("create", "node", "Service", "tier")) == "G3:none_in_flight"
    assert ow.m2("edge:OWNED_BY:vt", "operational").code == "APPLIED"
    assert guard(ow.m1("create", "edge", "OWNED_BY", "vt")) == "G7:state_allows_op"  # already operational


def test_m1_cooldown_blocks_a_retry_of_a_settled_key_until_it_elapses(ow):
    key = ow.logical_index("node", "Service", "tier", state="failed")
    out = ow.m1("create", "node", "Service", "tier")
    assert out.code == "REJECTED_GUARD" and out.detail["guard"] == "G7:state_allows_op"
    later = ow.ir.clock + 3_600_001
    assert ow.m1("create", "node", "Service", "tier", at=later).code == "APPLIED"
    assert ow.entry(key)["state"] == "requested"
    # a drop also waits for the cooldown of the settle that made the index operational
    ow.m2(key, "operational")
    assert ow.m1("drop", "node", "Service", "tier").code == "REJECTED_GUARD"
    assert ow.m1("drop", "node", "Service", "tier", at=ow.ir.clock + 3_600_001).code == "APPLIED"
    assert ow.entry(key)["state"] == "retiring"


def test_m1_budget_caps_the_optimizer_indexes(ow):
    for i in range(24):
        ow.logical_index("node", "Service", f"p{i}")
    out = ow.m1("create", "node", "Service", "p24")
    assert out.code == "REJECTED_GUARD" and out.detail["guard"] == "G7:state_allows_op"
    ow.m1("drop", "node", "Service", "p0", at=ow.ir.clock + 3_600_001)
    ow.m2("node:Service:p0", "retired")
    assert ow.m1("create", "node", "Service", "p24", at=ow.ir.clock + 10).code == "APPLIED"


@pytest.mark.parametrize("start,outcome,end", [
    ("requested", "operational", "operational"), ("requested", "failed", "failed"),
    ("retiring", "retired", "retired"), ("retiring", "failed", "operational")])
def test_m2_transitions(ow, start, outcome, end):
    key = ow.logical_index("node", "Service", "tier", state="requested" if start == "requested" else "operational")
    if start == "retiring":
        assert ow.m1("drop", "node", "Service", "tier", at=ow.ir.clock + 3_600_001).code == "APPLIED"
    assert ow.m2(key, outcome).code == "APPLIED"
    entry = ow.entry(key)
    assert entry["state"] == end and entry["settled_pt"] == ow.ir.clock and entry["ddl_ms"] == 7


def test_m2_refuses_incompatible_outcomes_the_wrong_caller_and_a_replayed_pid(ow):
    key = ow.logical_index("node", "Service", "tier", state="requested")
    for outcome in ("retired", "bogus"):
        out = ow.m2(key, outcome)
        assert out.code == "REJECTED_GUARD" and out.detail["guard"] == "G2:state_outcome_compatible"
    assert ow.m2(key, "operational", agent=OPT).code == "CALLER_DENIED"
    pid = f"settle:{key}:{ow.entry(key)['requested_seq']}"
    assert ow.m2(key, "operational", pid=pid).code == "APPLIED"
    assert ow.m2(key, "operational", pid=pid).code == "DUPLICATE"
    assert ow.m2(key, "failed", pid="settle-again").code == "REJECTED_GUARD"  # operational takes no outcome


# ───────────────────────────────────────────────────────────── the reconciler

def test_reconciler_action_table_is_the_16_11_table():
    from gnomon.ops.reconciler import action_for, physical_status

    assert [physical_status(x) for x in (None, "OPERATIONAL", "[Indexing] 0/6000: UNDER CONSTRUCTION", "FAILED")] == [
        "absent", "operational", "building", "failed"]
    table = {
        ("requested", "absent"): "create", ("requested", "building"): "poll",
        ("requested", "operational"): "settle_operational", ("requested", "failed"): "settle_failed",
        ("retiring", "operational"): "drop", ("retiring", "absent"): "settle_retired",
        ("operational", "absent"): "alert_out_of_band_drop", ("operational", "operational"): "none",
        (None, "operational"): "alert_out_of_band_create", ("retired", "operational"): "alert_out_of_band_create",
        (None, "absent"): "none", ("retired", "absent"): "none", ("failed", "absent"): "none"}
    for (state, phys), want in table.items():
        assert action_for(state, phys) == want, (state, phys)
    assert action_for("requested", "operational", genesis=True) == "settle_operational"
    assert action_for(None, "operational", genesis=True) == "none"


def test_reconciler_creates_polls_and_settles(ow):
    key = "edge:OWNED_BY:vt"
    assert ow.m1("create", "edge", "OWNED_BY", "vt").code == "APPLIED"
    seq = ow.entry(key)["requested_seq"]
    done = ow.reconciler().tick()
    assert [(d["key"], d["action"]) for d in done] == [(key, "create")]
    assert done[0]["outcome"].code == "APPLIED"
    assert key in ow.physical() and ow.entry(key)["state"] == "operational"
    assert ow.k.read("MATCH (r:Rewrite {id: $p}) RETURN r.rule", {"p": f"settle:{key}:{seq}"}) == [["IndexSettled"]]
    assert isinstance(ow.entry(key)["ddl_ms"], int)
    head = ow.k.head()["seq"]
    assert ow.reconciler().tick() == [] and ow.k.head()["seq"] == head  # idempotent


def test_reconciler_after_a_crash_between_ddl_and_m2_only_settles(ow):
    key = "edge:OWNED_BY:vt"
    ow.m1("create", "edge", "OWNED_BY", "vt")
    ow.k.graph.query("CREATE INDEX FOR ()-[f:OWNED_BY]-() ON (f.vt)")
    while key not in ow.physical():
        time.sleep(0.05)
    done = ow.reconciler().tick()
    assert [d["action"] for d in done] == ["settle_operational"] and ow.entry(key)["state"] == "operational"


def test_reconciler_drops_and_settles_a_retiring_index(ow):
    key = ow.build_index("node", "Service", "tier")
    assert key in ow.physical()
    assert ow.m1("drop", "node", "Service", "tier", at=ow.ir.clock + 3_600_001).code == "APPLIED"
    done = ow.reconciler().tick()
    assert [d["action"] for d in done] == ["drop"] and ow.entry(key)["state"] == "retired"
    assert key not in ow.physical()


def test_reconciler_drop_of_a_relationship_index_uses_the_for_pattern_form(ow):
    key = ow.build_index("edge", "OWNED_BY", "vt")
    ow.m1("drop", "edge", "OWNED_BY", "vt", at=ow.ir.clock + 3_600_001)
    ow.reconciler().tick()
    assert ow.entry(key)["state"] == "retired" and key not in ow.physical()  # DECISIONS D-6, FACTS F3


def test_reconciler_retiring_and_already_absent_only_settles(ow):
    key = ow.build_index("node", "Service", "tier")
    ow.m1("drop", "node", "Service", "tier", at=ow.ir.clock + 3_600_001)
    ow.k.graph.query("DROP INDEX ON :Service(tier)")
    done = ow.reconciler().tick()
    assert [d["action"] for d in done] == ["settle_retired"] and ow.entry(key)["state"] == "retired"


def test_reconciler_ddl_errors_settle_as_failed(ow):
    import redis

    class Broken:
        def run(self, graph, statement):
            raise redis.ResponseError("injected DDL failure")

    ow.m1("create", "node", "Service", "tier")
    ow.reconciler(ddl=Broken()).tick()
    entry = ow.entry("node:Service:tier")
    assert entry["state"] == "failed" and "injected" in entry["detail"]
    key = ow.build_index("edge", "OWNED_BY", "vt")
    ow.m1("drop", "edge", "OWNED_BY", "vt", at=ow.ir.clock + 3_600_001)
    ow.reconciler(ddl=Broken()).tick()
    assert ow.entry(key)["state"] == "operational" and key in ow.physical()  # a failed drop returns to operational


def test_reconciler_timeout_settles_as_failed(ow):
    from gnomon.ops.reconciler import Reconciler

    class Stuck(Reconciler):
        def physical(self):
            return {"edge:OWNED_BY:vt": "[Indexing] 1/2: UNDER CONSTRUCTION"}

    steps = iter(range(0, 10**9, 200_000))
    ow.m1("create", "edge", "OWNED_BY", "vt")
    Stuck(ow.db, ow.client, ow.k, agent=REC, mode="replay", clock=ow.tick, config=ow.cfg,
          monotonic=lambda: next(steps), sleep=lambda s: None).tick()
    entry = ow.entry("edge:OWNED_BY:vt")
    assert entry["state"] == "failed" and "timeout" in entry["detail"]


def test_reconciler_alerts_on_out_of_band_changes_and_writes_nothing(ow):
    key = ow.build_index("edge", "OWNED_BY", "vt")
    head = ow.k.head()["seq"]
    ow.k.graph.query("DROP INDEX FOR ()-[f:OWNED_BY]-() ON (f.vt)")
    ow.k.graph.query("CREATE INDEX FOR (n:Service) ON (n.tier)")
    rec = ow.reconciler()
    done = rec.tick()
    kinds = sorted((d["action"], d["key"]) for d in done)
    assert kinds == [("alert_out_of_band_create", "node:Service:tier"), ("alert_out_of_band_drop", key)]
    assert ow.k.head()["seq"] == head and sorted((a["kind"], a["key"]) for a in rec.alerts) == [
        ("out_of_band_create", "node:Service:tier"), ("out_of_band_drop", key)]


# ───────────────────────────────────────────────────────────── K8, MU24-MU25, the optimizer's limits

def test_k8_is_green_on_a_fresh_tenant_and_reports_both_directions(ow):
    from gnomon.audit import k8

    assert k8.check(ow.k.graph).ok
    ow.k.graph.query("CREATE INDEX FOR (n:Service) ON (n.tier)")
    key = ow.build_index("edge", "OWNED_BY", "vt")
    ow.k.graph.query("DROP INDEX FOR ()-[f:OWNED_BY]-() ON (f.vt)")
    ow.client.execute_command("GRAPH.CONSTRAINT", "DROP", ow.key, "MANDATORY", "NODE", "Rewrite", "PROPERTIES", "2",
                              "seq", "rule_sha")
    import time
    time.sleep(0.3)
    got = {(f["problem"], f["key"]) for f in k8.check(ow.k.graph).findings}
    assert got == {("index_unlogged", "node:Service:tier"), ("index_missing_physical", key),
                   ("constraint_missing_physical", "mandatory:node:Rewrite:seq,rule_sha")}
    ow.k.graph.query("CREATE INDEX FOR ()-[f:OWNED_BY]-() ON (f.vt)")


def test_k8_leaves_in_flight_entries_alone(ow):
    from gnomon.audit import k8

    ow.m1("create", "edge", "OWNED_BY", "vt")  # requested, not built
    assert k8.check(ow.k.graph).ok
    ow.k.graph.query("CREATE INDEX FOR ()-[f:OWNED_BY]-() ON (f.vt)")
    import time
    time.sleep(0.3)
    assert k8.check(ow.k.graph).ok  # built but M2 not yet applied: still in flight


def test_the_audit_runner_selects_k8_by_name(ow):
    from gnomon.audit.runner import audit

    report = audit(ow.db, ow.client, ow.key, checks=["K8"])
    assert list(report.results) == ["K8"] and report.ok


def test_mu24_and_mu25_are_detected_by_k8(ow):
    from gnomon.audit import mutations
    from gnomon.audit.runner import audit

    assert set(mutations.TC_MUTATIONS) == {"MU24", "MU25"}
    assert all(m.expected == ("K8",) for m in mutations.TC_MUTATIONS.values())
    scratch = f"{ow.key}:replay:mut-24"
    ow.client.execute_command("GRAPH.COPY", ow.key, scratch)
    with pytest.raises(mutations.MutationNotApplicable):  # no optimizer index to drop yet
        mutations.apply_mutation(ow.db, ow.client, scratch, "MU24")
    ow.client.execute_command("GRAPH.DELETE", scratch)
    ow.build_index("edge", "OWNED_BY", "vt")
    for mu in ("MU24", "MU25"):
        scratch = f"{ow.key}:replay:mut-{mu[2:]}"
        ow.client.execute_command("GRAPH.COPY", ow.key, scratch)
        try:
            assert audit(ow.db, ow.client, scratch, checks=["K8"]).ok
            m = mutations.apply_mutation(ow.db, ow.client, scratch, mu)
            import time
            time.sleep(0.3)
            report = audit(ow.db, ow.client, scratch, checks=["K8"])
            assert report.failed == list(m.expected), (mu, report.as_dict())
        finally:
            ow.client.execute_command("GRAPH.DELETE", scratch)


def test_the_optimizer_never_proposes_dropping_a_genesis_index(ow):
    ow.seed_shape("MATCH (s:Service {id: $id}) RETURN s.id", {"id": "s5"}, 5, 0.2)
    opt = ow.optimizer()
    for _ in range(ow.cfg.drop_windows + 1):
        run = opt.run()
        ow.ir.clock += ow.cfg.cooldown_ms
        assert all(c.get("op") != "drop" for c in run.candidates)
    assert ow.entry("edge:OWNED_BY:fid")["state"] == "operational"


def test_a_decision_to_drop_an_unused_optimizer_index_is_trialled_logged_and_executed(ow):
    from gnomon.audit.runner import audit

    key = ow.build_index("node", "Service", "tier")
    ow.ir.clock += 3_700_000
    ow.seed_shape("MATCH (s:Service {id: $id}) RETURN s.id", {"id": "s5"}, 10, 0.2)
    opt = ow.optimizer(config=cfg(**{**ow.cfg.__dict__, "kappa": 3, "margin_us": 0}))
    first = opt.run()
    assert first.winner is None and first.trace[-3:] == ["RETIRE_CHECK", "CLEANUP", "COOLDOWN"]
    ow.ir.clock += ow.cfg.cooldown_ms
    ow.seed_shape("MATCH (s:Service {id: $id}) RETURN s.id", {"id": "s5"}, 10, 0.2)
    second = opt.run()
    assert second.trace[:6] == ["IDLE", "SAMPLE", "BASELINE", "DETECT", "GENERATE", "TRIAL"], second.trace
    assert second.winner["key"] == key and second.winner["op"] == "drop" and second.envelope["kind"] == "index"
    assert ow.entry(key)["state"] == "retiring"
    ow.reconciler().tick()
    opt.resume(second)
    assert ow.entry(key)["state"] == "retired" and key not in ow.physical()
    assert audit(ow.db, ow.client, ow.key, checks=["K7", "K8"]).ok


# ───────────────────────────────────────────────────────────── K7 for index decisions

def settled_run(ow, **kw):
    opt = ow.optimizer(**kw)
    run = opt.run()
    ow.reconciler().tick()
    opt.resume(run)
    return run


def test_k7_re_derives_an_index_decision_from_the_log(ow):
    from gnomon.audit.runner import audit

    ow.reads(30)
    run = settled_run(ow)
    report = audit(ow.db, ow.client, ow.key, checks=["K7"])
    assert report.ok, report.as_dict()
    assert run.envelope["did"] in {r[0] for r in ow.k.read("MATCH (x:Decision) RETURN x.id")}


@pytest.mark.parametrize("tamper,problem", [
    (lambda e: e["candidates"][1].update(score_num=e["candidates"][1]["score_num"] + 1), "score_mismatch"),
    (lambda e: e["measurements"]["candidates"]["1"]["read"].popitem(), "measure_digest_mismatch"),
    (lambda e: e["candidates"][1].update(plan_after="0" * 64), "plan_after_mismatch"),
    (lambda e: e["candidates"][1].update(plan_before="0" * 64), "plan_before_mismatch"),
    (lambda e: e.update(workload_digest="0" * 64), "workload_digest_mismatch"),
    (lambda e: e["candidates"].__setitem__(0, {**e["candidates"][0], "won": True}), "winner_mismatch")])
def test_k7_catches_a_tampered_index_decision(ow, tamper, problem):
    from gnomon.audit import k7
    from gnomon.executive import envelope

    ow.reads(30)
    opt = ow.optimizer()
    run = opt.run(commit=False)
    assert run.state == "DECIDED" and run.winner is not None
    env = json.loads(json.dumps(run.envelope))
    tamper(env)
    sealed = envelope.seal({k: v for k, v in env.items() if k != "sha"})
    assert ow.m1("create", "edge", "OWNED_BY", "vf", env=sealed).code in ("APPLIED", "REJECTED_DECISION")
    if ow.entry(Q5_VF) is None:
        pytest.fail("the tampered decision was not committed; the test must exercise K7")
    findings = k7.check(ow.k.graph, db=ow.db, client=ow.client).findings
    assert problem in {f["problem"] for f in findings}, findings


# ───────────────────────────────────────────────────────────── state hash and ledger determinism (K8 audit suite)

def test_physical_indexes_never_enter_the_canonical_state_hash(ow):
    import time

    from gnomon.audit.canonical import graph_lines, hash_lines

    before = hash_lines(graph_lines(ow.k.graph))
    twin = f"{ow.key}:twin"
    ow.client.execute_command("GRAPH.COPY", ow.key, twin)
    g = ow.db.select_graph(twin)
    g.query("CREATE INDEX FOR ()-[f:OWNED_BY]-() ON (f.vt)")
    g.query("CREATE INDEX FOR (n:Service) ON (n.tier)")
    time.sleep(0.5)
    assert hash_lines(graph_lines(g)) == before


def test_replay_reproduces_the_state_hash_after_a_dynamic_index_change(ow):
    from gnomon.audit.runner import audit

    ow.reads(30)
    settled_run(ow)
    assert Q5_VF in ow.physical()
    report = audit(ow.db, ow.client, ow.key, checks=["K1", "K2", "K4", "K6", "K8"])
    assert report.ok, report.as_dict()  # the replay target has no physical vf index and the same hash


def test_the_ledger_is_deterministic_across_reruns_with_recorded_measurements(make_ow):
    from gnomon.audit.replay import replay
    from gnomon.audit.canonical import graph_lines, hash_lines

    heads, shas = [], []
    for _ in range(2):
        w = make_ow()
        w.seed_q5(30)
        run = w.optimizer(measurer=RecordedMeasurer()).run()
        assert run.outcome.code == "APPLIED"
        heads.append(w.k.head()["hash"])
        shas.append(run.envelope["sha"])
        w.reconciler().tick()
        scratch = f"{w.key}:replay:det"
        replay(w.db, w.client, w.key, until=w.k.head()["seq"], target=scratch)
        assert hash_lines(graph_lines(w.db.select_graph(scratch))) == hash_lines(graph_lines(w.k.graph))
        w.client.execute_command("GRAPH.DELETE", scratch)
    assert heads[0] == heads[1] and shas[0] == shas[1]


# ───────────────────────────────────────────────────────────── proof C

@pytest.mark.e2e
@pytest.mark.timeout(900)
def test_proof_c_a_slow_read_becomes_a_logged_index_and_the_audits_stay_green(ow, request):
    """§26 row C: Q5 slow (traverse + filter, measured µs) → trials on shadows → M1 → reconciler DDL → M2 →
    Edge By Index Scan with measured µs → K1-K8 green."""
    from gnomon.audit.runner import audit

    def median_us(n=15):
        text = Q5_FILE.read_text(encoding="utf-8")
        lo = 1_791_000_000_000 - 1_000_000_000 + 300_000
        samples = sorted(round(ow.k.graph.ro_query(text, {"INF": INF, "T1": lo, "T2": lo + 20_000}).run_time_ms * 1000)
                         for _ in range(n))
        return samples[n // 2]

    ow.reads(40)
    before = median_us()
    opt = ow.optimizer()
    run = opt.run()
    assert run.winner["key"] == Q5_VF
    ow.reconciler().tick()
    opt.resume(run)
    after = median_us()
    request.node.user_properties.append(("q5_median_us", {"before": before, "after": after}))
    assert run.verify["ok"] and after < before, (before, after)
    assert ow.entry(Q5_VF)["state"] == "operational" and Q5_VF in ow.physical()
    report = audit(ow.db, ow.client, ow.key, checks=["K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8"])
    assert report.ok, report.as_dict()
