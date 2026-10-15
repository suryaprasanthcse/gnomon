"""The atomic apply query (master §2.5, §6.2, §6.6): seq, tick, chain, KeyVersion, CAUSED_BY; rollback; replay."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1b]


def test_apply_noop_increments_seq_and_chains(genesis, ref_chain, chain_of):
    h0 = genesis.head()
    first = genesis.submit("Noop", {"note": "a", "key": "k1", "tags": ["t1"]}, pid="pid-a", view_keys=["Head"])
    assert first.code == "APPLIED" and first.seq == 2

    h1 = genesis.head()
    assert h1["seq"] == 2 and h1["hash"] == first.hash
    assert (h1["pt"], h1["lc"]) > (h0["pt"], h0["lc"])

    r = chain_of(genesis.graph)[-1]
    assert (r["id"], r["seq"], r["rule"], r["agent"], r["read_seq"]) == ("pid-a", 2, "Noop", "kernel", 1)
    assert (r["pt"], r["lc"]) == (h1["pt"], h1["lc"])
    assert r["prev"] == h0["hash"]
    assert r["hash"] == ref_chain(r) == first.hash
    assert r["write_keys"] == ["Noop:*", "Noop:k1", "Tag:t1"]
    assert r["view_keys"] == ["Head"]
    assert r["params"] == '{"key":"k1","note":"a","tags":["t1"]}'

    assert genesis.read("MATCH (n:Noop) RETURN n.pid, n.seq, n.pt, n.lc, n.note") == [
        ["pid-a", 2, h1["pt"], h1["lc"], "a"]
    ]
    assert genesis.read("MATCH (k:KeyVersion) WHERE k.key IN ['Noop:*', 'Noop:k1', 'Tag:t1', 'Head'] "
                        "RETURN k.key, k.seq, k.rw ORDER BY k.key") == [
        ["Head", 1, "genesis"], ["Noop:*", 2, "pid-a"], ["Noop:k1", 2, "pid-a"], ["Tag:t1", 2, "pid-a"],
    ]
    # The last writer of every view key becomes a cause, whether or not it changed.
    assert genesis.read("MATCH (:Rewrite {id: 'pid-a'})-[:CAUSED_BY]->(c:Rewrite) RETURN c.id") == [["genesis"]]

    second = genesis.submit("Noop", {"note": "b"}, pid="pid-b", view_keys=["Noop:k1", "Tag:t1", "Never:seen"])
    assert second.code == "APPLIED" and second.seq == 3
    r2 = chain_of(genesis.graph)[-1]
    assert r2["prev"] == first.hash and r2["hash"] == ref_chain(r2) == genesis.head()["hash"]
    assert genesis.read("MATCH (:Rewrite {id: 'pid-b'})-[:CAUSED_BY]->(c:Rewrite) RETURN c.id") == [["pid-a"]]


def test_caused_by_points_backwards(genesis):
    for n in range(4):
        assert genesis.submit("Noop", {"note": str(n), "key": "k"}, pid=f"p{n}", view_keys=["Noop:k", "Head"]).code \
            == "APPLIED"
    edges = genesis.read("MATCH (a:Rewrite)-[:CAUSED_BY]->(b:Rewrite) RETURN a.seq, b.seq ORDER BY a.seq, b.seq")
    assert edges == [[2, 1], [3, 1], [3, 2], [4, 1], [4, 3], [5, 1], [5, 4]]
    assert all(a > b for a, b in edges)


def test_error_rolls_back(genesis, snap):
    before = snap(genesis.graph)
    outcome = genesis.submit("Noop", {"note": "x", "boom": True}, pid="pid-boom", view_keys=["Head"])
    assert outcome.code == "KERNEL_ERROR"
    assert "DIGEST_BAD_FIELD" in outcome.detail["error"]
    assert snap(genesis.graph) == before
    assert genesis.read("MATCH (n:Noop) RETURN count(n)") == [[0]]
    assert genesis.read("MATCH (r:Rewrite {id: 'pid-boom'}) RETURN count(r)") == [[0]]


def test_replay_regression_is_a_kernel_error(genesis, snap):
    head = genesis.head()
    before = snap(genesis.graph)
    outcome = genesis.submit("Noop", {"note": "x"}, pid="pid-old", mode="replay", rec_pt=head["pt"], rec_lc=head["lc"])
    assert outcome.code == "KERNEL_ERROR" and "HLC_REGRESSION" in outcome.detail["error"]
    assert snap(genesis.graph) == before


@pytest.mark.parametrize("mode", ["replay", "sim"])
def test_replay_mode_uses_recorded_clock(genesis, chain_of, mode):
    head = genesis.head()
    rec = (head["pt"] + 12_345, 7)
    outcome = genesis.submit("Noop", {"note": "r"}, pid=f"pid-{mode}", mode=mode, rec_pt=rec[0], rec_lc=rec[1])
    assert outcome.code == "APPLIED"
    r = chain_of(genesis.graph)[-1]
    assert (r["pt"], r["lc"]) == rec
    after = genesis.head()
    assert (after["pt"], after["lc"]) == rec
    assert genesis.read("MATCH (n:Noop) RETURN n.pt, n.lc") == [list(rec)]


def test_live_tick_follows_head(genesis):
    a = genesis.submit("Noop", {"note": "1"}, pid="l1")
    b = genesis.submit("Noop", {"note": "2"}, pid="l2")
    assert a.code == b.code == "APPLIED"
    rows = genesis.read("MATCH (r:Rewrite) WHERE r.seq > 1 RETURN r.pt, r.lc ORDER BY r.seq")
    assert rows[0] < rows[1]
