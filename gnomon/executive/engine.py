"""Simulation, scoring and envelope assembly (master §15.3-§15.9), shared by the Executive and by audit K7.

Everything here works on graphs it is given: a base snapshot and branches copied from it. Nothing writes live.
`run` is the whole counterfactual pass over a set of candidates; K7 calls it again on a base rebuilt from the log, so
a recorded decision re-simulates by construction when it is honest and differs when it is not.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from gnomon.executive import scoring
from gnomon.executive.branches import Base, BranchManager
from gnomon.kernel.outcomes import Code
from gnomon.kernel.service import KernelHandle, KernelService, Proposal

ESCALATE = "EscalateIncident"
ENUMERATED = ("IsolateService", "FailoverService", "RollbackDeploy", "RestoreService")  # the order of the candidate set
SCORE = "MATCH (i:Incident {id: $incident})-[:AFFECTS]->(s:Service) RETURN @LIB@.blastRadius(s, $hops, {federation: $federation})"


@dataclass
class Result:
    """One candidate after simulation: its outcome, and its scorer output when it was scored."""

    k: int
    rule: str
    params: dict
    outcome: str  # scored | guard_pruned | error
    reason: str | None = None
    out: dict | None = None
    metrics: dict | None = None
    branch: str | None = None
    branch_seq: int | None = None


@dataclass
class Run:
    base: Base
    base_out: dict
    results: list[Result] = field(default_factory=list)


def score_graph(graph, incident: str, hops: int, library: str, federation: bool = False) -> dict:
    """Exec.blastRadius from the incident's affected service, as a read-only query on `graph` (scorer v2 when federated)."""
    rows = graph.ro_query(SCORE.replace("@LIB@", library),
                          {"incident": incident, "hops": hops, "federation": bool(federation)}).result_set
    if not rows:
        raise LookupError(f"incident {incident!r} affects no service in {graph.name}")
    return rows[0][0]


def sim_apply(db, registry, base: Base, branch: str, did: str, attempt: int, k: int, rule: str, params: Mapping,
              agent: str) -> Result:
    """The rule's body on a branch in sim mode: no P1, no E0, `$agent` the Executive, `$pid = sim:<did>:<attempt>:<k>`."""
    result = Result(k, rule, dict(params), "error", branch=branch)
    try:
        registry.get(rule)
    except KeyError as exc:
        result.reason = str(exc.args[0])
        return result
    outcome = KernelService(db.select_graph(branch), registry).submit(Proposal(
        rule=rule, p=dict(params), pid=f"sim:{did}:{attempt}:{k}", agent=agent, read_seq=base.seq, view_keys=(),
        mode="sim", rec_pt=base.pt, rec_lc=base.lc + 1))
    if outcome.code == Code.APPLIED:
        result.outcome, result.branch_seq = "scored", outcome.seq
    elif outcome.code == Code.REJECTED_GUARD:
        result.outcome, result.reason = "guard_pruned", outcome.detail["guard"]
    else:
        result.reason = f"{outcome.code.value}: {outcome.detail}"
    return result


def run(db, client, registry, base: Base, did: str, attempt: int, incident: str, candidates: Sequence[Mapping],
        library: str, agent: str, hops: int = scoring.HOPS, keep: bool = True, federation: bool = False) -> Run:
    """Score the base, then simulate and score every candidate on its own branch (candidates carry their k)."""
    manager = BranchManager(db, client, base.key[: base.key.index(":sim:")])
    out = Run(base, score_graph(db.select_graph(base.key), incident, hops, library, federation))
    for cand in candidates:
        branch = manager.branch(base, cand["k"])
        result = sim_apply(db, registry, base, branch, did, attempt, cand["k"], cand["rule"], cand["params"], agent)
        if result.outcome == "scored":
            result.out = score_graph(db.select_graph(branch), incident, hops, library, federation)
            result.metrics = scoring.score(out.base_out, result.out)
        if not keep:
            manager.retire([branch])
            result.branch = None
        out.results.append(result)
    return out


def enumerate_actions(db, registry, base: Base, incident: str) -> list[dict]:
    """The enabled action tuples on the base (§15.3 step 1), in rule then query order, with their view keys."""
    from gnomon.query.service import QueryService

    qs = QueryService(KernelHandle(db.select_graph(base.key), registry))
    sess = qs.session("executive")
    found = []
    for rule in ENUMERATED:
        read = qs.list_actions(sess, rule, {"incident": incident})
        for tuple_, keys in zip(read.tuples, read.tuple_keys):
            found.append({"rule": rule, "params": {k: v for k, v in tuple_.items() if k != "index"},
                          "keys": list(keys)})
    return found


def candidate_set(incident: str, tuples: Sequence[Mapping], nominations: Sequence[Mapping], k_max: int,
                  escalate_only: bool = False) -> tuple[str, list[dict], list[dict]]:
    """(mode, candidates, dropped): Escalate is k = 0; all tuples when at most K_MAX, else the nominations (§15.3)."""
    escalate = {"k": 0, "rule": ESCALATE, "params": {"incident": incident}, "keys": []}
    if escalate_only:
        return "escalate_only", [escalate], []
    if len(tuples) <= k_max:
        return "exhaustive", [escalate, *({**t, "k": i} for i, t in enumerate(tuples, 1))], []
    by_identity = {(t["rule"], tuple(sorted(t["params"].items()))): t for t in tuples}
    chosen, dropped = [], []
    for nomination in nominations:
        identity = (nomination["rule"], tuple(sorted(nomination["params"].items())))
        match = by_identity.get(identity)
        if match is None:
            dropped.append({"rule": nomination["rule"], "params": dict(nomination["params"])})
        elif match not in chosen and len(chosen) < k_max - 1:
            chosen.append(match)
    return "nominated", [escalate, *({**t, "k": i} for i, t in enumerate(chosen, 1))], dropped


def candidate_rows(results: Sequence[Result], vetoed: set[str]) -> list[dict]:
    """The envelope's candidate entries (§14.1); a vetoed candidate keeps its scores and is never selected."""
    rows = []
    for r in results:
        row = {"k": r.k, "rule": r.rule, "params": r.params, "params_sha": scoring.params_sha(r.rule, r.params),
               "outcome": r.outcome, "reason": r.reason, "benefit": None, "harm": None, "teams_new": None,
               "score_num": None, "won": False}
        if r.outcome == "scored":
            row.update(r.metrics)
            if row["params_sha"] in vetoed:
                row["outcome"] = "vetoed"
        rows.append(row)
    k = scoring.select(rows)
    for row in rows:
        row["won"] = row["k"] == k
    return rows


def digest(fields: Sequence[object]) -> str:
    """Kernel.digest (§8.2): SHA-256 over the length-prefixed fields; used for footprint_digest."""
    out = []
    for x in fields:
        if x is None:
            out.append("~,")
        else:
            text = str(x)
            out.append(f"{len(text.encode('utf-8'))}:{text},")
    return hashlib.sha256("".join(out).encode("utf-8")).hexdigest()


def impacted_digest(base_out: Mapping) -> str:
    return hashlib.sha256(repr(base_out["impacted"]).encode("utf-8")).hexdigest()


def baseline(base_out: Mapping) -> dict:
    return {"impacted_digest": impacted_digest(base_out), "teams": list(base_out["teams"]),
            "region_size": len(base_out["region"])}
