"""Counterfactual time travel (master §19.3): what would the Executive have done at an earlier position of the log?

`counterfactual(did, at_seq=S)` rebuilds the base at seq S by replaying the live log, re-runs the Executive's simulation
and scoring on the decision's recorded candidate set (the same rules and parameters, nothing re-enumerated), and reports
the recorded decision and the new one side by side. It reuses the decision's scorer library, scorer version (federated or
not), hops and integer configuration, so a difference between the two tables is caused only by the world at S. Nothing
is written to the live graph; the replay and the branches are scratch graphs, deleted before the function returns.
"""

from __future__ import annotations

import json

from gnomon.audit.k7_resim import ACTIVE_SCORER, EXECUTIVE_AGENT, RULES_AT

DECISION = """MATCH (x:Decision {id: $did})-[:COMMITTED_AS]->(r:Rewrite)
WHERE x.kind = 'action' AND x.scorer ENDS WITH '.blastRadius'
RETURN r.seq, r.attach"""
VIEW = ("k", "rule", "params", "outcome", "reason", "benefit", "harm", "teams_new", "score_num", "won")


def _table(rows) -> list[dict]:
    return [{f: row.get(f) for f in VIEW} for row in rows]


def _winner(rows) -> dict | None:
    return next(({f: r.get(f) for f in ("k", "rule", "params", "params_sha", "score_num")} for r in rows if r.get("won")),
                None)


def counterfactual(db, client, live: str, did: str, at_seq: int) -> dict:
    """Both tables and both winners for decision `did` with the world rebuilt at `at_seq`. `error` names why a
    counterfactual cannot be made (unknown decision, a seq outside the log, no such incident at that seq)."""
    from gnomon.audit.replay import Divergence, replay, rule_from_node
    from gnomon.executive import engine
    from gnomon.executive.branches import BranchManager
    from gnomon.kernel.registry import Registry

    graph = db.select_graph(live)
    found = graph.ro_query(DECISION, {"did": did}).result_set
    if not found:
        return {"did": did, "at_seq": at_seq, "error": f"no committed action decision {did!r} in {live}"}
    commit_seq, attach = found[0]
    recorded = json.loads(attach)
    head = graph.ro_query("MATCH (h:Head) RETURN h.seq").result_set[0][0]
    out = {"did": did, "at_seq": at_seq,
           "original": {"read_seq": recorded["read_seq"], "committed_seq": commit_seq,
                        "winner": _winner(recorded["candidates"]), "candidates": _table(recorded["candidates"])}}
    if not isinstance(at_seq, int) or isinstance(at_seq, bool) or not 1 <= at_seq <= head:
        out["error"] = f"--at-seq must be between 1 and the head seq {head}, got {at_seq!r}"
        return out
    _, version = graph.ro_query(ACTIVE_SCORER, {"name": recorded["scorer"], "sha": recorded["scorer_sha"],
                                                "seq": commit_seq}).result_set[0]
    library = recorded["scorer"].rsplit(".", 1)[0]
    incident = recorded["candidates"][0]["params"]["incident"]
    target = f"{live}:sim:cf{at_seq}:{did}:base"
    manager = BranchManager(db, client, live)
    try:
        try:
            replay(db, client, live, until=at_seq, target=target)
        except Divergence as exc:
            out["error"] = f"the base at seq {at_seq} does not replay: {exc.as_dict()}"
            return out
        base = manager.verify(target)
        if not base.ok:
            out["error"] = f"the base at seq {at_seq} failed VERIFY-BASE"
            return out
        base_graph = db.select_graph(target)
        registry = Registry([rule_from_node(props) for (props,) in
                             base_graph.ro_query(RULES_AT, {"seq": base.seq}).result_set])
        agents = base_graph.ro_query(EXECUTIVE_AGENT).result_set
        agent = agents[0][0] if agents else recorded["proposer"].removeprefix("agent:")
        candidates = [{"k": c["k"], "rule": c["rule"], "params": c["params"]} for c in recorded["candidates"]]
        try:
            run = engine.run(db, client, registry, base, f"cf-{did}", recorded["attempt"], incident, candidates,
                             library, agent, recorded["hops"], keep=False, federation=(version or 1) >= 2)
        except LookupError as exc:
            out["error"] = str(exc)
            return out
        rows = engine.candidate_rows(run.results, set())
        out["counterfactual"] = {"read_seq": base.seq, "winner": _winner(rows), "candidates": _table(rows),
                                 "base_teams": list(run.base_out["teams"])}
        was, now = out["original"]["winner"], out["counterfactual"]["winner"]
        out["differs"] = (was or {}).get("params_sha") != (now or {}).get("params_sha")
        return out
    finally:
        prefix = f"{live}:sim:cf{at_seq}:{did}:"
        manager.retire([g for g in client.execute_command("GRAPH.LIST") if g.startswith(prefix)])
