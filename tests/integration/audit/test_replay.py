"""Replay engine (master §12.1): the log replayed into a fresh graph reproduces every step and the whole state."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g3]

REWRITE_FIELDS = ("seq", "id", "pt", "lc", "rule", "rule_sha", "agent", "params", "read_seq", "view_keys",
                  "write_keys", "prev", "hash")


def rewrites(graph):
    cols = ", ".join(f"r.{f}" for f in REWRITE_FIELDS)
    return graph.ro_query(f"MATCH (r:Rewrite) RETURN {cols} ORDER BY r.seq").result_set


def causal_edges(graph):
    rows = graph.ro_query("MATCH (r:Rewrite)-[:CAUSED_BY]->(x:Rewrite) RETURN r.seq, x.seq").result_set
    return sorted(tuple(row) for row in rows)


def seq_of(graph, pid):
    return graph.ro_query("MATCH (r:Rewrite {id: $pid}) RETURN r.seq", {"pid": pid}).result_set[0][0]


def test_replay_reproduces_each_step(db, redis_raw, scenario):
    from gnomon.audit.replay import replay

    result = replay(db, redis_raw, scenario.key)
    try:
        assert result.key == f"{scenario.key}:replay:{scenario.head_seq}"
        assert (result.seq, result.hash) == (scenario.head_seq, scenario.head_hash)
        live, again = db.select_graph(scenario.key), db.select_graph(result.key)
        assert rewrites(again) == rewrites(live)
        assert len(rewrites(live)) == scenario.head_seq
        # recorded view keys re-validate and recreate CAUSED_BY
        assert causal_edges(live), "the scenario must contain causal edges"
        assert causal_edges(again) == causal_edges(live)
        assert redis_raw.execute_command("GRAPH.LIST").count(result.key) == 1
    finally:
        redis_raw.execute_command("GRAPH.DELETE", result.key)


def test_replay_until_a_prefix(db, redis_raw, scenario):
    from gnomon.audit.replay import replay

    until = scenario.head_seq - 3
    live = db.select_graph(scenario.key)
    expected = live.ro_query("MATCH (r:Rewrite {seq: $s}) RETURN r.hash", {"s": until}).result_set[0][0]
    result = replay(db, redis_raw, scenario.key, until=until, target=f"{scenario.key}:replay:a07-prefix")
    try:
        assert (result.key, result.seq, result.hash) == (f"{scenario.key}:replay:a07-prefix", until, expected)
        head = db.select_graph(result.key).ro_query("MATCH (h:Head) RETURN h.seq, h.hash").result_set[0]
        assert head == [until, expected]
    finally:
        redis_raw.execute_command("GRAPH.DELETE", result.key)


def test_replay_overwrites_a_stale_scratch_graph(db, redis_raw, scenario):
    from gnomon.audit.replay import replay

    target = f"{scenario.key}:replay:a07-stale"
    db.select_graph(target).query("CREATE (:Leftover {x: 1})")
    result = replay(db, redis_raw, scenario.key, target=target)
    try:
        assert db.select_graph(target).ro_query("MATCH (n:Leftover) RETURN count(n)").result_set[0][0] == 0
        assert result.seq == scenario.head_seq
    finally:
        redis_raw.execute_command("GRAPH.DELETE", target)


def test_replay_raises_divergence_on_a_tampered_step(db, redis_raw, scratch):
    from gnomon.audit.replay import Divergence, replay

    graph = db.select_graph(scratch)
    seq = seq_of(graph, "co-2")
    graph.query("MATCH (r:Rewrite {id: 'co-2'}) SET r.params = replace(r.params, 'looked fine', 'looked bad')")
    with pytest.raises(Divergence) as caught:
        replay(db, redis_raw, scratch, target=f"{scratch}:replay:a07-div")
    assert caught.value.seq == seq
    assert caught.value.reason == "mismatch"
    assert caught.value.expected["hash"] != caught.value.actual["hash"]
    assert str(seq) in str(caught.value)


def test_replay_revalidates_recorded_view_keys(db, redis_raw, scratch):
    """A recorded read_seq older than a recorded view key's version is refused by P0 during replay."""
    from gnomon.audit.replay import Divergence, replay

    graph = db.select_graph(scratch)
    seq = seq_of(graph, "cl-1")
    graph.query("MATCH (r:Rewrite {id: 'cl-1'}) SET r.read_seq = r.read_seq - 1")
    with pytest.raises(Divergence) as caught:
        replay(db, redis_raw, scratch, target=f"{scratch}:replay:a07-stale-view")
    assert (caught.value.seq, caught.value.reason) == (seq, "rejected")


def test_replay_reports_a_missing_step(db, redis_raw, scratch):
    from gnomon.audit.replay import Divergence, replay

    graph = db.select_graph(scratch)
    seq = seq_of(graph, "co-2")
    graph.query("MATCH (r:Rewrite {id: 'co-2'}) DETACH DELETE r")
    with pytest.raises(Divergence) as caught:
        replay(db, redis_raw, scratch, target=f"{scratch}:replay:a07-gap")
    assert (caught.value.seq, caught.value.reason) == (seq, "missing")


def test_replay_reports_a_missing_rule(db, redis_raw, scratch):
    from gnomon.audit.replay import Divergence, replay

    graph = db.select_graph(scratch)
    seq = seq_of(graph, "co-2")
    graph.query("MATCH (r:Rewrite {id: 'co-2'}) SET r.rule_sha = 'f' + substring(r.rule_sha, 1)")
    with pytest.raises(Divergence) as caught:
        replay(db, redis_raw, scratch, target=f"{scratch}:replay:a07-norule")
    assert (caught.value.seq, caught.value.reason) == (seq, "missing")


def test_replay_reports_a_kernel_error(db, redis_raw, scratch):
    """A recorded clock that runs backwards makes Kernel.tick throw HLC_REGRESSION during replay."""
    from gnomon.audit.replay import Divergence, replay

    graph = db.select_graph(scratch)
    seq = seq_of(graph, "co-2")
    graph.query("MATCH (r:Rewrite {id: 'co-2'}) SET r.pt = 1")
    with pytest.raises(Divergence) as caught:
        replay(db, redis_raw, scratch, target=f"{scratch}:replay:a07-err")
    assert (caught.value.seq, caught.value.reason) == (seq, "error")
    assert "HLC_REGRESSION" in caught.value.detail


def test_independent_replays_have_equal_canonical_hashes(db, redis_raw, scenario):
    from gnomon.audit.canonical import state_hash
    from gnomon.audit.replay import replay

    first = replay(db, redis_raw, scenario.key, target=f"{scenario.key}:replay:a07-1")
    second = replay(db, redis_raw, scenario.key, target=f"{scenario.key}:replay:a07-2")
    try:
        live = state_hash(db.select_graph(scenario.key))
        assert state_hash(db.select_graph(first.key)) == live
        assert state_hash(db.select_graph(second.key)) == live
    finally:
        for key in (first.key, second.key):
            redis_raw.execute_command("GRAPH.DELETE", key)
