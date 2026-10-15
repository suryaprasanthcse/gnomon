"""Builds a dependency graph straight from a spec (tests/fixtures/graphs/blast_*.yaml) for the Exec.blastRadius tests.

The scorer reads only Service nodes (uid, criticality, health, mode), DEP_NOW edges (fid, hard) and OWNER_NOW edges to
Team nodes, so these tests need no kernel: they create exactly those elements and call the UDF.
"""

from __future__ import annotations

from pathlib import Path

import yaml

GRAPHS = Path(__file__).resolve().parents[1] / "fixtures" / "graphs"


def load_spec(name: str) -> dict:
    return yaml.safe_load((GRAPHS / f"{name}.yaml").read_text(encoding="utf-8"))


def build(graph, spec: dict) -> None:
    for sid, s in spec["services"].items():
        graph.query("CREATE (:Service:Entity {uid: $uid, id: $id, criticality: $crit, health: $health, mode: $mode})",
                    {"uid": f"svc:{sid}", "id": sid, "crit": s["crit"], "health": s["health"], "mode": s["mode"]})
    for dependent, dependency, hard, fid in spec["edges"]:
        graph.query("MATCH (a:Service {id: $a}), (b:Service {id: $b}) CREATE (a)-[:DEP_NOW {fid: $fid, hard: $hard}]->(b)",
                    {"a": dependent, "b": dependency, "fid": fid, "hard": hard})
    for sid, team in spec["owners"].items():
        graph.query("MATCH (s:Service {id: $s}) MERGE (t:Team:Entity {uid: $t}) "
                    "CREATE (s)-[:OWNER_NOW {fid: $fid}]->(t)", {"s": sid, "t": f"team:{team}", "fid": f"o-{sid}"})


def blast(graph, library: str, seed: str, hops: int, federation: bool = False) -> dict:
    rows = graph.ro_query(f"MATCH (s:Service {{uid: $uid}}) RETURN {library}.blastRadius(s, $h, {{federation: $f}})",
                          {"uid": f"svc:{seed}", "h": hops, "f": federation}).result_set
    return rows[0][0]
