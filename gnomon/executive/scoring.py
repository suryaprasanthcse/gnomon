"""Scoring, selection, footprint and evidence (master §15.7-§15.9): integer arithmetic over scorer outputs.

`B` is the base's impacted weights and `K_k` a branch's. benefit_k sums what the action removes, harm_k what it adds
(plus TAU per newly impacted team), and score_num_k = benefit_k * LAMBDA_D - LAMBDA_N * harm_k, so the score is
score_num / LAMBDA_D and lambda = LAMBDA_N / LAMBDA_D = 3/2: one unit of harm costs 1.5 units of benefit.
Nothing here is floating point; two runs on equal outputs agree to the last bit.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from gnomon.kernel.jcs import canonicalize

# Appendix C
W = 2520
TAU = 2520
LAMBDA_N = 3
LAMBDA_D = 2
MARGIN = 1260
EVIDENCE_N = 3
K_MAX = 8
HOPS = 4
STALE_RETRIES = 2
SIM_TTL_MS = 600_000

ROLES = ("rescued", "harmed", "owners")


def weights(output: Mapping) -> dict[str, int]:
    """{uid: w} of a scorer output's impacted rows."""
    return {row[0]: row[3] for row in output["impacted"]}


def score(base: Mapping, branch: Mapping) -> dict[str, int]:
    """benefit, harm, teams_new and score_num of one branch against the base (§15.7)."""
    b, k = weights(base), weights(branch)
    new_teams = set(branch["teams"]) - set(base["teams"])
    benefit = sum(max(0, w - k.get(u, 0)) for u, w in b.items())
    harm = sum(max(0, w - b.get(u, 0)) for u, w in k.items()) + TAU * len(new_teams)
    return {"benefit": benefit, "harm": harm, "teams_new": len(new_teams),
            "score_num": benefit * LAMBDA_D - LAMBDA_N * harm}


def params_sha(rule: str, params: Mapping) -> str:
    """The identity of a candidate action: SHA-256 of the rule name and its parameters, canonically."""
    return hashlib.sha256(canonicalize({"rule": rule, "params": dict(params)}).encode("utf-8")).hexdigest()


def select(candidates: Sequence[Mapping], *, margin: int = MARGIN, lambda_d: int = LAMBDA_D) -> int:
    """The winning k: highest score_num among scored candidates, ties to the lowest params_sha; a winner that is not
    the Escalate bar (k = 0) and scores below MARGIN * LAMBDA_D falls back to Escalate."""
    scored = [c for c in candidates if c["outcome"] == "scored"]
    if not scored:
        return 0
    winner = min(scored, key=lambda c: (-c["score_num"], c["params_sha"]))
    if winner["k"] != 0 and winner["score_num"] < margin * lambda_d:
        return 0
    return winner["k"]


def _service_keys(uid: str) -> list[str]:
    x = uid.split(":", 1)[1]
    return [f"Service:{x}:state", f"DEPENDS_ON:{uid}:*", f"DEPENDS_ON:*:{uid}", f"OWNED_BY:{uid}:*",
            f"DEPLOYED:{uid}:*", f"REPLICA_OF:*:{uid}"]


def _federated_keys(uid: str) -> list[str]:
    """The T-D/T-E keys of a region node (§15.8, §18): a Consumer's subscription and its tenant's inbound channel, an
    ExternalService proxy's state and the dependencies on it, and for a Service the subscriptions to it."""
    kind, _, rest = uid.partition(":")
    if kind == "cons":
        return [f"CONSUMES:{uid}:*", f"Channel:in:{rest.split('/', 1)[0]}"]
    if kind == "ext":
        return [f"ExternalService:{rest}:state", f"DEPENDS_ON:*:{uid}"]
    if kind == "svc":
        return [f"CONSUMES:*:{uid}"]
    return []


def footprint(base: Mapping, incident: str, federation: bool = False) -> list[str]:
    """The simulation footprint (§15.8): everything in the base region a change could touch, plus the incident. With
    `federation` it also holds the consumer, channel and proxy keys, so a subscription that lands mid-decision makes the
    decision stale (§18)."""
    keys = {f"Incident:{incident}:state", f"ROOT_CAUSE:inc:{incident}:*"}
    for uid in base["region"]:
        if uid.startswith("svc:"):
            keys.update(_service_keys(uid))
        if federation:
            keys.update(_federated_keys(uid))
    return sorted(keys)


def _walk(uid: str, parents: Mapping[str, tuple]) -> tuple[list[str], list[dict]]:
    """The path from a source to `uid` along best-path parents, and the cite of each hop (dependent, edge fid)."""
    path, cites = [uid], []
    while parents.get(uid, (None, None))[0] is not None:
        parent, fid = parents[uid]
        cites.append({"s": uid, "fid": fid})
        uid = parent
        path.append(uid)
    return path[::-1], cites[::-1]


def _top(diffs: Mapping[str, int], n: int) -> list[str]:
    return [u for u, _ in sorted(((u, d) for u, d in diffs.items() if d > 0), key=lambda x: (-x[1], x[0]))[:n]]


def evidence(base: Mapping, winner: Mapping, algo: str = "Exec.blastRadius", n: int = EVIDENCE_N):
    """(evidence, cites) for the winning branch (§15.9): the rescued set walked on the base, the harmed set walked on
    the winner, and the owners of every service on those paths. `cites` is the sorted union of the evidence cites."""
    b, k = weights(base), weights(winner)
    base_parent = {row[0]: (row[4], row[5]) for row in base["impacted"]}
    win_parent = {row[0]: (row[4], row[5]) for row in winner["impacted"]}
    items: list[dict] = []
    on_path: set[str] = set()
    for role, diffs, parents in (("rescued", {u: b[u] - k.get(u, 0) for u in b}, base_parent),
                                 ("harmed", {u: k[u] - b.get(u, 0) for u in k}, win_parent)):
        for uid in _top(diffs, n):
            path, cites = _walk(uid, parents)
            on_path.update(path)
            items.append({"algo": algo, "role": role, "path": path, "cites": cites})
    owners = sorted({tuple(e) for out in (base, winner) for e in out["owner_edges"] if e[0] in on_path})
    seen: set[str] = set()
    for svc, team, fid in owners:
        if (svc, team, fid) in seen:
            continue
        seen.add((svc, team, fid))
        items.append({"algo": algo, "role": "owners", "path": [svc, team], "cites": [{"s": svc, "fid": fid}]})
    for ord_, item in enumerate(items):
        item["ord"] = ord_
    cites = {(c["s"], c["fid"]) for item in items for c in item["cites"]}
    return items, [{"s": s, "fid": f} for s, f in sorted(cites)]
