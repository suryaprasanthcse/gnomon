"""F5 · GRAPH.PROFILE line format parses as documented in master §16.3."""

import re

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]

# line := indent op_name ( " | " argument )? ( " | Records produced: " int ", Execution time: " float " ms" )?
LINE = re.compile(
    r"^(?P<indent>(?:    )*)(?P<op>[A-Za-z][A-Za-z ]*?)"
    r"(?: \| (?P<arg>(?!Records produced:)[^|]+?))?"
    r"(?: \| Records produced: (?P<records>\d+), Execution time: (?P<ms>[0-9.]+) ms)?$"
)


def test_f05_profile_lines_follow_the_documented_grammar(graph, tenant, redis_raw, facts):
    graph.query("UNWIND range(1, 200) AS i CREATE (:A {v: i})-[:R]->(:B {v: i})")
    lines = redis_raw.execute_command(
        "GRAPH.PROFILE", tenant, "MATCH (a:A)-[:R]->(b:B) WHERE a.v > 10 RETURN b.v ORDER BY b.v"
    )
    parsed = [LINE.match(line) for line in lines]
    all_parse = all(m is not None and m.group("records") is not None for m in parsed)
    depths = [len(m.group("indent")) // 4 for m in parsed if m]
    nested = depths == list(range(len(depths)))  # this plan is a single chain
    scan = next((m for m in parsed if m and m.group("op") == "Node By Label Scan"), None)

    facts.expect(
        "F5",
        all_parse and nested and scan is not None and scan.group("arg") == "(a:A)" and int(scan.group("records")) == 200,
    )
