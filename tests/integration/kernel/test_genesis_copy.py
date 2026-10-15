"""Harness patch P3: a tenant made by GRAPH.COPY of golden_genesis equals a freshly bootstrapped one."""

import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1b]


def kernel_state(graph):
    head = graph.query("MATCH (h:Head) RETURN h.tenant, h.seq, h.pt, h.lc, h.hash, h.kernel_sha").result_set
    rules = graph.query("MATCH (r:Rule) RETURN r.name, r.sha, r.active ORDER BY r.name").result_set
    entries = graph.query("MATCH (i:IndexEntry) RETURN i.key, i.state, i.settled_seq ORDER BY i.key").result_set
    cons = graph.query("MATCH (c:ConstraintEntry) RETURN c.key, c.state ORDER BY c.key").result_set
    counts = graph.query("MATCH (n) RETURN count(n)").result_set
    return head, rules, entries, cons, counts


def physical(graph):
    idx = graph.query("CALL db.indexes() YIELD label, properties, entitytype, status "
                      "RETURN label, properties, entitytype, status").result_set
    cons = graph.query("CALL db.constraints() YIELD type, label, properties, entitytype, status "
                       "RETURN type, label, properties, entitytype, status").result_set
    return sorted(map(repr, idx)), sorted(map(repr, cons))


def test_copied_genesis_equals_fresh_genesis(genesis, db, redis_raw, tenant, record_property, genesis_config):
    from gnomon.bootstrap.genesis import bootstrap

    fresh_key = f"{tenant}:fresh"
    started = time.perf_counter()
    outcome = bootstrap(
        db.select_graph(fresh_key), redis_raw, genesis_config["registry"], tenant=genesis_config["tenant"],
        agents=genesis_config["agents"], skills=genesis_config["skills"], pid="genesis", mode="replay",
        rec_pt=1_791_000_000_000, rec_lc=0,
    )
    fresh_ms = round((time.perf_counter() - started) * 1000, 2)
    record_property("fresh_genesis_ms", fresh_ms)
    print(f"fresh bootstrap {fresh_ms} ms; copy setup recorded as genesis_setup_ms")
    assert outcome.code == "APPLIED"

    fresh, copied = db.select_graph(fresh_key), genesis.graph
    assert kernel_state(copied) == kernel_state(fresh)
    assert kernel_state(copied)[0][0][1] == 1  # seq 1
    copied_phys, fresh_phys = physical(copied), physical(fresh)
    assert copied_phys == fresh_phys
    assert all("OPERATIONAL" in row for row in copied_phys[0] + copied_phys[1])

    # The copy is independent: writing to it leaves the fresh tenant untouched (F1).
    assert genesis.submit("Noop", {"note": "x"}, pid="copy-1").code == "APPLIED"
    assert kernel_state(fresh)[0][0][1] == 1
