"""S21 · Write-query timeout semantics (private server: changes GRAPH.CONFIG)."""

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]

SLOW_READ = "UNWIND range(1, 20000000) AS x WITH x WHERE x % 7 = 0 RETURN count(x)"
SLOW_WRITE_ROWS = 600_000
SLOW_WRITE = f"UNWIND range(1, {SLOW_WRITE_ROWS}) AS x CREATE (:T {{v: x}})"


def _config(client, name: str) -> int:
    return int(client.execute_command("GRAPH.CONFIG", "GET", name)[1])


def test_s21_timeouts_for_reads_and_writes(private_falkordb, error_of, facts):
    client = private_falkordb.client
    defaults_off = _config(client, "TIMEOUT_DEFAULT") == 0 and _config(client, "TIMEOUT_MAX") == 0
    count = lambda g: g.query("MATCH (t:T) RETURN count(t)").result_set[0][0]

    g1 = private_falkordb.graph()
    read_error = error_of(lambda: g1.query(SLOW_READ, timeout=50))
    per_query_write_error = error_of(lambda: g1.query(SLOW_WRITE, timeout=50))
    per_query_write_rows = count(g1)
    g1.delete()

    g2 = private_falkordb.graph()
    client.execute_command("GRAPH.CONFIG", "SET", "TIMEOUT_DEFAULT", "50")
    try:
        default_write_error = error_of(lambda: g2.query(SLOW_WRITE))
    finally:
        client.execute_command("GRAPH.CONFIG", "SET", "TIMEOUT_DEFAULT", "0")
    default_write_rows = count(g2)
    g2.delete()

    # Observed: a per-query TIMEOUT stops reads but is ignored by writes; TIMEOUT_DEFAULT stops writes
    # too, and a timed-out write leaves nothing behind. Both defaults ship as 0 (no timeout).
    facts.expect(
        "S21",
        defaults_off
        and read_error is not None and "timed out" in read_error
        and per_query_write_error is None and per_query_write_rows == SLOW_WRITE_ROWS
        and default_write_error is not None and "timed out" in default_write_error
        and default_write_rows == 0,
    )
