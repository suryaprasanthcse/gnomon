"""Synthetic company generator (master §11.4): from a seed, one tenant's services, teams, ownership, deploys.

A `World` is plain data: 40 services in a DAG with hard and soft dependency edges, criticality from a fixed
distribution, 8 teams, ownership histories with backdated changes, deploy histories with exactly one bad deploy
(always the latest deploy of a service that others depend on), and replicas, one of them unhealthy. `apply_world`
loads it into a tenant through the kernel in replay mode, so a load is bit-identical for the same seed (C8).
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field

EPOCH_MS = 1_791_000_000_000  # genesis pt of every test tenant
LOAD_PT = EPOCH_MS + 1_000_000_000  # the "now" the world is loaded at; every vf is earlier (backdated)
CRITICALITY = ([1, 2, 3, 4, 5], [10, 25, 30, 20, 15])
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS


@dataclass(frozen=True)
class World:
    seed: int
    services: tuple
    teams: tuple
    edges: tuple  # (dependent, dependency, hard)
    ownership: tuple
    deploys: tuple
    replicas: tuple
    bad_deploy: dict
    load_pt: int = LOAD_PT

    def digest(self) -> str:
        text = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate(seed: int, n_services: int = 40, n_teams: int = 8) -> World:
    rng = random.Random(seed)
    services = tuple({"id": f"s{i:02d}", "name": f"service-{i:02d}", "tier": rng.choice(["t1", "t2", "t3"]),
                      "criticality": rng.choices(*CRITICALITY)[0], "user_facing": rng.random() < 0.4}
                     for i in range(n_services))
    teams = tuple(f"t{j + 1}" for j in range(n_teams))
    ids = [s["id"] for s in services]
    edges = []
    for i, a in enumerate(ids[:-1]):
        pool = ids[i + 1:i + 7]
        for b in rng.sample(pool, k=min(rng.randint(1, 2), len(pool))):
            edges.append((a, b, rng.random() < 0.6))
    if not any(h for *_, h in edges):
        edges[0] = (edges[0][0], edges[0][1], True)
    if all(h for *_, h in edges):
        edges[-1] = (edges[-1][0], edges[-1][1], False)

    ownership = []
    for k, sid in enumerate(ids):
        first = rng.choice(teams)
        vf = LOAD_PT - rng.randint(30 * DAY_MS, 60 * DAY_MS)
        ownership.append({"service": sid, "team": first, "vf": vf, "supersede": False, "old_team": None})
        if k % 4 == 0:  # a reorganisation: a later, backdated hand-over to another team
            ownership.append({"service": sid, "team": rng.choice([t for t in teams if t != first]),
                              "vf": vf + rng.randint(5 * DAY_MS, 20 * DAY_MS), "supersede": True, "old_team": first})

    dependency_targets = sorted({b for _, b, hard in edges if hard})
    bad_service = rng.choice(dependency_targets)
    deploys = []
    for sid in ids:
        for n in range(rng.randint(1, 3)):
            deploys.append({"id": f"d-{sid}-{n + 1}", "service": sid, "version": f"1.{n}.0", "verified": True,
                            "expected_health": "ok", "vf": LOAD_PT - (40 - 10 * n) * DAY_MS, "bad": False})
    bad_id = f"d-{bad_service}-bad"
    deploys.append({"id": bad_id, "service": bad_service, "version": "2.0.0", "verified": False,
                    "expected_health": "ok", "vf": LOAD_PT - HOUR_MS, "bad": True})

    replicas = [{"service": sid, "id": f"{sid}-r{k}", "healthy": True} for sid in ids for k in (1, 2)]
    replicas[rng.randrange(len(replicas))]["healthy"] = False
    return World(seed=seed, services=services, teams=teams, edges=tuple(edges), ownership=tuple(ownership),
                 deploys=tuple(deploys), replicas=tuple(replicas),
                 bad_deploy={"id": bad_id, "service": bad_service, "version": "2.0.0"})


@dataclass
class LoadReport:
    proposals: int = 0
    rejected: list = field(default_factory=list)
    ownership: dict = field(default_factory=dict)  # service -> [(team, vf, seq)] in assertion order
    end_pt: int = 0


def apply_world(kernel, world: World, *, agent: str = "ops-1") -> LoadReport:
    """Load the world through the kernel (replay mode, one proposal every 10 ms from `world.load_pt`)."""
    report = LoadReport()
    pt = max(world.load_pt, kernel.head().get("pt", 0))

    def propose(rule: str, p: dict, pid: str):
        nonlocal pt
        pt += 10
        out = kernel.submit(rule, p, pid=pid, agent=agent, mode="replay", rec_pt=pt, rec_lc=0)
        report.proposals += 1
        if out.code != "APPLIED":
            report.rejected.append((rule, pid, out.code, out.detail))
        return out

    for t in world.teams:
        propose("RegisterEntity.Team", {"id": t, "name": t}, f"w-team-{t}")
    for s in world.services:
        propose("RegisterEntity.Service", {k: s[k] for k in ("id", "name", "tier", "criticality", "user_facing")},
                f"w-svc-{s['id']}")
    deploys = sorted(world.deploys, key=lambda d: (d["service"], d["vf"], d["id"]))
    for d in deploys:
        propose("RegisterEntity.Deploy", {k: d[k] for k in ("id", "service", "version", "verified", "expected_health")},
                f"w-dep-{d['id']}")
    previous: dict[str, str] = {}
    for d in deploys:
        p = {"s": f"svc:{d['service']}", "o": f"dep:{d['id']}", "vf": d["vf"], "supersede": d["service"] in previous}
        if d["service"] in previous:
            p["old_o"] = f"dep:{previous[d['service']]}"
        propose("AssertFact.DEPLOYED", p, f"w-deployed-{d['id']}")
        previous[d["service"]] = d["id"]
    for a, b, hard in world.edges:
        propose("AssertFact.DEPENDS_ON", {"s": f"svc:{a}", "o": f"svc:{b}", "vf": world.load_pt - 90 * DAY_MS,
                                          "supersede": False, "hard": hard}, f"w-dep-{a}-{b}")
    for n, o in enumerate(world.ownership):
        p = {"s": f"svc:{o['service']}", "o": f"team:{o['team']}", "vf": o["vf"], "supersede": o["supersede"]}
        if o["supersede"]:
            p["old_o"] = f"team:{o['old_team']}"
        out = propose("AssertFact.OWNED_BY", p, f"w-own-{n:03d}")
        report.ownership.setdefault(o["service"], []).append((o["team"], o["vf"], out.seq))
    report.end_pt = pt
    return report
