"""K7 for decisions of kind `index` (master §16.14): an index decision re-derives from the log.

1 the scorer's source hash is the recorded one, and the workload and every candidate's samples hash to their digests;
2 medians, MADs and every candidate's integer scores are recomputed from the recorded raw samples, and so is the winner;
3 the base is rebuilt at `read_seq` (physical optimizer indexes included), EXPLAIN of every recorded shape gives the
  recorded `plan_before`, and on a copy with each candidate's DDL applied the recorded `plan_after`, exactly.
The measurements themselves are attested, not reproducible (§28); everything computed from them is.
"""

from __future__ import annotations

import json

from gnomon.audit.k8 import physical_indexes
from gnomon.optimizer import cost, detect, scorer, trials
from gnomon.optimizer.config import OptConfig
from gnomon.ops.reconciler import create_statement

INDEX_DECISIONS = """MATCH (x:Decision)-[:COMMITTED_AS]->(r:Rewrite)
WHERE x.kind = 'index'
RETURN x.id AS id, r.id AS rewrite, r.seq AS seq, r.attach AS attach
ORDER BY seq"""
OPTIMIZER_INDEXES = ("MATCH (x:IndexEntry) WHERE x.origin = 'optimizer' AND x.state = 'operational' "
                     "RETURN x.entity, x.label, x.prop ORDER BY x.key")
ROW_KEYS = ("benefit", "harm", "score_num")


def recompute(d: dict, k: int) -> cost.Score:
    """The integer score of candidate k from the samples recorded in the envelope."""
    m = d["measurements"]
    config = OptConfig(**m["config"])
    freq = {s["shape_sha"]: s["freq"] for s in d["workload"]["shapes"]}
    wfreq = d["workload"]["write_mix"]
    rec = m["candidates"][str(k)]
    row = next(c for c in d["candidates"] if c["k"] == k)
    base_write = {r: v for r, v in m["base"]["write"].items() if r in rec["write"]}
    if row["op"] == "create":
        return cost.score(m["base"]["read"], rec["read"], freq, base_write, rec["write"], wfreq, rec["build_us"], config)
    return cost.score_drop(m["base"]["read"], rec["read"], freq, base_write, rec["write"], wfreq, config)


def expected_winner(d: dict) -> int | None:
    margin = d["measurements"]["config"]["margin_us"]
    eligible = [c for c in d["candidates"] if c["k"] > 0 and c.get("outcome") == "scored"
                and cost.accept(True, recompute(d, c["k"]).net_us, margin)]
    best = min(eligible, key=lambda c: (-recompute(d, c["k"]).net_us, c["params_sha"]), default=None)
    return None if best is None else best["k"]


def _restore_optimizer_indexes(db, client, graph) -> None:
    """The base of a replay has the logical catalogue only; give it the optimizer indexes the log says exist."""
    present = physical_indexes(graph)
    for entity, label, prop in graph.ro_query(OPTIMIZER_INDEXES).result_set:
        if f"{entity}:{label}:{prop}" not in present:
            graph.query(create_statement(entity, label, prop))
    import time

    deadline = time.monotonic() + 120
    while any(s != "OPERATIONAL" for s in physical_indexes(graph).values()) and time.monotonic() < deadline:
        time.sleep(0.05)


def check(graph, db, client) -> list[dict]:
    from gnomon.audit.replay import Divergence, replay
    from gnomon.executive.branches import BranchManager

    findings: list[dict] = []
    manager = BranchManager(db, client, graph.name)
    for did, rewrite, seq, attach in graph.ro_query(INDEX_DECISIONS).result_set:
        d = json.loads(attach)

        def find(problem: str, **detail) -> None:
            findings.append({"problem": problem, "id": did, "rewrite": rewrite, **detail})

        if d["scorer"] != scorer.NAME or d["scorer_sha"] != scorer.sha():
            find("scorer_sha_mismatch", recorded=d["scorer_sha"], current=scorer.sha())
        if cost.digest(d["workload"]) != d["workload_digest"]:
            find("workload_digest_mismatch")
        if d["measurements"]["config"]["margin_us"] != d["margin"]:
            find("config_mismatch", fields=["margin"])
        for c in d["candidates"]:
            if c["k"] == 0 or c.get("outcome") != "scored":
                continue
            record = d["measurements"]["candidates"].get(str(c["k"]))
            if record is None or cost.digest(record) != c.get("measure_digest"):
                find("measure_digest_mismatch", k=c["k"])
                continue
            score = recompute(d, c["k"])
            got = {"benefit": score.gain_us, "harm": score.write_us + score.build_amort_us + score.mem_us,
                   "score_num": score.net_us}
            bad = [f for f in ROW_KEYS if c.get(f) != got[f]]
            if bad:
                find("score_mismatch", k=c["k"], fields=bad)
        recorded_winners = sorted(c["k"] for c in d["candidates"] if c.get("won"))
        want = expected_winner(d)
        if recorded_winners != ([] if want is None else [want]):
            find("winner_mismatch", recorded=recorded_winners, recomputed=want)
        _plans(graph, db, client, manager, d, find, replay, Divergence)
    return findings


def _plans(graph, db, client, manager, d, find, replay, Divergence) -> None:
    base_key = f"{graph.name}:opt:k7:{d['did']}:base"
    try:
        try:
            replay(db, client, graph.name, until=d["read_seq"], target=base_key)
        except Divergence as exc:
            find("base_divergence", detail=exc.as_dict())
            return
        base = manager.verify(base_key)
        if not base.ok:
            find("base_unverified", gapless=base.gapless, linked=base.linked)
            return
        base_graph = db.select_graph(base_key)
        _restore_optimizer_indexes(db, client, base_graph)
        shapes = d["workload"]["shapes"]
        before = [trials.explain(client, base_key, s["cypher"]).sha for s in shapes]
        for c in d["candidates"]:
            if "plan_before" not in c:
                continue
            if cost.digest(before) != c["plan_before"]:
                find("plan_before_mismatch", k=c["k"])
            shadow = f"{graph.name}:opt:k7:{d['did']}:{c['k']}"
            try:
                trials.copy(client, base_key, shadow)
                p = c["params"]
                trials.apply_ddl(db, client, shadow, detect.candidate_of("K7", p["entity"], p["label"], p["prop"], p["op"]),
                                 d["measurements"]["config"]["ddl_timeout_ms"])
                after = [trials.explain(client, shadow, s["cypher"]).sha for s in shapes]
            except trials.TrialError as exc:
                find("plan_after_unreproducible", k=c["k"], error=str(exc)[:200])
                continue
            finally:
                trials.remove(client, shadow)
            if cost.digest(after) != c["plan_after"]:
                find("plan_after_mismatch", k=c["k"])
    finally:
        trials.delete_all(client, f"{graph.name}:opt:k7:{d['did']}:")
