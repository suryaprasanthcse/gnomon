"""Container smoke tests: the real FalkorDB image behind the session fixtures (RUNBOOK_A §4.2, contract C5)."""

import json
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_h0]

ROOT = Path(__file__).resolve().parents[2]
SMOKE_UDF = ROOT / "tests" / "fixtures" / "udf" / "smoke.js"


def test_ping(falkordb_container, redis_raw):
    assert falkordb_container.host
    assert falkordb_container.port > 0
    assert redis_raw.ping() is True


def test_graph_roundtrip(db, tenant):
    graph = db.select_graph(tenant)
    graph.query("CREATE (:X {v: 1})")
    result = graph.query("MATCH (x:X) RETURN x.v")
    assert result.result_set == [[1]]


def test_tenant_cleanup(falkordb_container, redis_raw, pytester, monkeypatch):
    """Runs a real test that uses the `tenant` fixture in a child pytest, then checks GRAPH.LIST."""
    record = pytester.path / "created.json"
    monkeypatch.setenv("GNOMON_FALKORDB_URL", f"{falkordb_container.host}:{falkordb_container.port}")
    monkeypatch.setenv("GNOMON_CLEANUP_RECORD", str(record))
    pytester.makepyprojecttoml((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytester.makeconftest((ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))
    pytester.makepyfile(
        test_inner=textwrap.dedent(
            """
            import json
            import os


            def test_creates_live_and_scratch(redis_raw, tenant):
                scratch = tenant + ":sim:x"
                redis_raw.execute_command("GRAPH.QUERY", tenant, "CREATE (:Live)")
                redis_raw.execute_command("GRAPH.QUERY", scratch, "CREATE (:Scratch)")
                listed = set(redis_raw.execute_command("GRAPH.LIST"))
                assert {tenant, scratch} <= listed
                with open(os.environ["GNOMON_CLEANUP_RECORD"], "w", encoding="utf-8") as fh:
                    json.dump([tenant, scratch], fh)
            """
        )
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)

    created = json.loads(record.read_text(encoding="utf-8"))
    assert created[0].startswith("t:test-")
    remaining = set(redis_raw.execute_command("GRAPH.LIST"))
    assert remaining.isdisjoint(created)


def test_udf_load_roundtrip(redis_raw, tenant):
    source = SMOKE_UDF.read_text(encoding="utf-8")
    redis_raw.execute_command("GRAPH.UDF", "LOAD", "REPLACE", "Smoke", source)
    try:
        reply = redis_raw.execute_command("GRAPH.QUERY", tenant, "RETURN Smoke.greet('gnomon')")
        assert reply[1] == [["hello gnomon"]]
    finally:
        redis_raw.execute_command("GRAPH.UDF", "DELETE", "Smoke")
    assert "Smoke" not in repr(redis_raw.execute_command("GRAPH.UDF", "LIST"))
