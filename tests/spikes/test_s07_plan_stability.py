"""S7 · Are PROFILE and EXPLAIN outputs stable enough to parse? Compared with plans recorded at A01."""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]

PLANS = Path(__file__).resolve().parents[1] / "fixtures" / "plans"
METRICS = re.compile(r"^Records produced: (\d+), Execution time: ([0-9.]+) ms$")


def parse_plan(lines: list[str]) -> list[tuple[int, str, str | None]]:
    tree = []
    for line in lines:
        body = line.lstrip(" ")
        indent = len(line) - len(body)
        assert indent % 4 == 0, line
        parts = body.split(" | ")
        if METRICS.match(parts[-1]):
            parts.pop()
        assert 1 <= len(parts) <= 2, line
        tree.append((indent // 4, parts[0], parts[1] if len(parts) == 2 else None))
    return tree


def recorded(name: str) -> list[tuple[int, str, str | None]]:
    return parse_plan((PLANS / name).read_text(encoding="utf-8").splitlines())


def test_s07_explain_is_stable_and_matches_recorded_plans(graph, tenant, redis_raw, wait_operational, facts):
    graph.query("UNWIND range(1, 200) AS i CREATE (:A {v: i})-[:R]->(:B {v: i})")
    query = "MATCH (a:A)-[:R]->(b:B) WHERE a.v > 10 RETURN b.v ORDER BY b.v"
    runs = [tuple(redis_raw.execute_command("GRAPH.EXPLAIN", tenant, query)) for _ in range(5)]
    stable = len(set(runs)) == 1
    traverse_matches = parse_plan(list(runs[0])) == recorded("explain_traverse_filter.txt")

    graph.query(
        "UNWIND range(1, 500) AS i CREATE (:Service {id: 's' + toString(i)})"
        "-[:OWNED_BY {fid: 'f' + toString(i), te: 9007199254740991, vf: i}]->(:Team {id: 't' + toString(i % 8)})"
    )
    graph.query("CREATE (:Head {seq: 1})")
    graph.query("CREATE INDEX FOR ()-[f:OWNED_BY]-() ON (f.vf)")
    wait_operational(graph)
    q5 = (
        "CYPHER INF=9007199254740991 T1=10 T2=20 MATCH (h:Head) MATCH (s:Service)-[f:OWNED_BY]->(t:Team) "
        "WHERE f.te = $INF AND f.vf >= $T1 AND f.vf < $T2 "
        "RETURN h.seq AS read_seq, s.id AS service, t.id AS team, f.vf AS changed_at, f.fid AS fid ORDER BY changed_at"
    )
    q5_matches = parse_plan(redis_raw.execute_command("GRAPH.EXPLAIN", tenant, q5)) == recorded("explain_q5_edge_index.txt")

    facts.expect("S7", stable and traverse_matches and q5_matches)
