"""S16 · Internal execution time in GRAPH.QUERY statistics."""

import re

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]

INTERNAL_TIME = re.compile(r"^Query internal execution time: ([0-9.]+) milliseconds$")


def test_s16_query_statistics_carry_internal_execution_time(redis_raw, tenant, facts):
    timings = []
    for cypher in ("CREATE (:X {v: 1})", "MATCH (x:X) RETURN x.v", "MATCH (x:X) SET x.v = 2"):
        stats = redis_raw.execute_command("GRAPH.QUERY", tenant, cypher)[-1]
        found = [float(m.group(1)) for line in stats if (m := INTERNAL_TIME.match(line))]
        timings.append(found[0] if found else None)
    facts.expect("S16", all(t is not None and t >= 0 for t in timings))
