"""P5 · Determinism and tamper evidence (master §26): nothing changes without a trace."""

import json

import pytest

from gnomon.audit.canonical import state_hash
from gnomon.audit.replay import replay
from gnomon.audit.runner import audit

pytestmark = [pytest.mark.e2e, pytest.mark.gate_g4b]


def test_replay_into_a_fresh_graph_gives_an_equal_state_hash(core_run, db, redis_raw):
    out = replay(db, redis_raw, core_run.kernel.graph.name)
    try:
        assert out.hash == state_hash(core_run.kernel.graph) == core_run.state_hash
    finally:
        redis_raw.execute_command("GRAPH.DELETE", out.key)


def scratch(core_run, redis_raw, db, suffix):
    name = f"{core_run.kernel.graph.name}:p5{suffix}"
    redis_raw.execute_command("GRAPH.COPY", core_run.kernel.graph.name, name)
    return name, db.select_graph(name)


def test_an_out_of_band_edit_is_named_by_k4(core_run, db, redis_raw):
    name, graph = scratch(core_run, redis_raw, db, "a")
    try:
        graph.query("MATCH (s:Service) WITH s ORDER BY s.id LIMIT 1 SET s.tier = 'tampered'")
        report = audit(db, redis_raw, name, ["K4"])
        assert not report.results["K4"].ok and "tier" in json.dumps(report.results["K4"].findings)
    finally:
        redis_raw.execute_command("GRAPH.DELETE", name)


def test_edited_params_are_named_by_k1(core_run, db, redis_raw):
    name, graph = scratch(core_run, redis_raw, db, "b")
    try:
        seq = core_run.kernel.read("MATCH (r:Rewrite {rule: 'ProposeCause'}) RETURN r.seq ORDER BY r.seq LIMIT 1")[0][0]
        graph.query("MATCH (r:Rewrite {seq: $s}) SET r.params = '{}'", {"s": seq})
        report = audit(db, redis_raw, name, ["K1"])
        assert not report.results["K1"].ok and any(seq in f.values() for f in report.results["K1"].findings)
    finally:
        redis_raw.execute_command("GRAPH.DELETE", name)
