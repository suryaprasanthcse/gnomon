"""Kernel.invariantProbe (master §8.6) and the WHY-entity provenance query (§21.3), plus the gk CLI."""

import json

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g3]

INF = 9007199254740991


def probe_all(graph):
    """Every probe kind over every node it applies to; the list of violations."""
    out = []
    for (v,) in graph.ro_query("MATCH (t:Task) RETURN Kernel.invariantProbe('lease', t, {})").result_set:
        out += v
    fts = graph.ro_query("MATCH (ft:FactType) RETURN ft.rel, ft.cardinality, ft.projection ORDER BY ft.rel").result_set
    for rel, cardinality, proj in fts:
        args = {"rel": rel, "functional": cardinality == "functional"}
        for (v,) in graph.ro_query("MATCH (s:Entity) RETURN Kernel.invariantProbe('overlap', s, $a)",
                                   {"a": args}).result_set:
            out += v
        if proj:
            for (v,) in graph.ro_query("MATCH (s:Entity) RETURN Kernel.invariantProbe('projection', s, $a)",
                                       {"a": {"rel": rel, "proj": proj}}).result_set:
                out += v
    for (v,) in graph.ro_query("MATCH (r:Rewrite) RETURN Kernel.invariantProbe('causal', r, {})").result_set:
        out += v
    return out


def test_invariant_probe_kinds_clean(db, scenario):
    assert probe_all(db.select_graph(scenario.key)) == []


def test_invariant_probe_lease(db, scratch):
    graph = db.select_graph(scratch)
    graph.query("MATCH (a:Agent {id: 'tri-2'}), (t:Task {id: 't1'}) CREATE (a)-[:HOLDS {from_seq: 1}]->(t)")
    uid = graph.ro_query("MATCH (t:Task {id: 't1'}) RETURN t.uid").result_set[0][0]
    assert probe_all(graph) == [{"kind": "lease", "subject": uid, "detail": {"count": 2}}]


def test_invariant_probe_overlap(db, scratch):
    graph = db.select_graph(scratch)
    fid, vf = graph.ro_query("MATCH (:Service {id: 'a'})-[f:OWNED_BY]->() RETURN f.fid, f.vf").result_set[0]
    graph.query("MATCH (s:Service {id: 'a'}), (t:Team {id: 't1'}) "
                "CREATE (s)-[:OWNED_BY {fid: 'forged#0', vf: $vf + 1, vt: $vf + 5, ts: 1, te: $inf}]->(t)",
                {"vf": vf, "inf": INF})
    assert probe_all(graph) == [{"kind": "overlap", "subject": "svc:a",
                                 "detail": {"rel": "OWNED_BY", "a": fid, "b": "forged#0"}}]


def test_invariant_probe_overlap_set_form_groups_by_object(db, scratch):
    graph = db.select_graph(scratch)
    vf = graph.ro_query("MATCH (:Service {id: 'a'})-[f:DEPENDS_ON]->() RETURN f.vf").result_set[0][0]
    # same subject, different object: no overlap for a set fact
    graph.query("MATCH (s:Service {id: 'a'}), (t:Service {id: 'a'}) "
                "CREATE (s)-[:DEPENDS_ON {fid: 'self#0', vf: $vf, vt: $vf + 5, ts: 1, te: $inf}]->(t)",
                {"vf": vf, "inf": INF})
    assert probe_all(graph) == []


def test_invariant_probe_projection(db, scratch):
    graph = db.select_graph(scratch)
    fid = graph.ro_query("MATCH (:Service {id: 'a'})-[p:DEP_NOW]->() RETURN p.fid").result_set[0][0]
    graph.query("MATCH (:Service {id: 'a'})-[p:DEP_NOW]->() DELETE p")
    graph.query("MATCH (s:Service {id: 'a'}), (t:Team {id: 't1'}) CREATE (s)-[:OWNER_NOW {fid: 'ghost#0'}]->(t)")
    owned = graph.ro_query("MATCH (:Service {id: 'a'})-[f:OWNED_BY]->() RETURN f.fid").result_set[0][0]
    violations = sorted(probe_all(graph), key=lambda v: v["detail"]["rel"])
    assert violations == [
        {"kind": "projection", "subject": "svc:a",
         "detail": {"rel": "DEPENDS_ON", "proj": "DEP_NOW", "missing": [fid], "orphan": []}},
        {"kind": "projection", "subject": "svc:a",
         "detail": {"rel": "OWNED_BY", "proj": "OWNER_NOW", "missing": [], "orphan": ["ghost#0"]}},
    ]
    assert owned != "ghost#0"


def test_invariant_probe_causal(db, scratch):
    graph = db.select_graph(scratch)
    graph.query("MATCH (a:Rewrite {seq: 2}), (b:Rewrite {id: 'pc-1'}) CREATE (a)-[:CAUSED_BY]->(b)")
    last = graph.ro_query("MATCH (r:Rewrite {id: 'pc-1'}) RETURN r.seq").result_set[0][0]
    rid = graph.ro_query("MATCH (r:Rewrite {seq: 2}) RETURN r.id").result_set[0][0]
    assert probe_all(graph) == [{"kind": "causal", "subject": rid, "detail": {"cause": "pc-1", "cause_seq": last}}]


def test_invariant_probe_rejects_an_unknown_kind(db, scenario):
    import redis

    with pytest.raises(redis.ResponseError, match="BAD_PROBE"):
        db.select_graph(scenario.key).ro_query("MATCH (h:Head) RETURN Kernel.invariantProbe('nope', h, {})")


def test_why_entity(db, scenario):
    from gnomon.audit.runner import why_entity

    graph = db.select_graph(scenario.key)
    pu, cl = (graph.ro_query("MATCH (r:Rewrite {id: $p}) RETURN r.seq", {"p": p}).result_set[0][0]
              for p in ("pu-1", "cl-1"))
    direct = {row[0] for row in graph.ro_query(
        "MATCH (:Rewrite {id: 'pu-1'})-[:CAUSED_BY]->(x:Rewrite) RETURN x.seq").result_set}
    why = why_entity(graph, scenario.message_uid)
    assert (why["entity"], why["created_at"], why["rule"], why["agent"]) == ("msg:m1", pu, "PostUpdate", "com-1")
    assert cl in direct
    assert why["causes"] == sorted(why["causes"])
    assert {chain[1] for chain in why["causes"] if len(chain) == 2} == direct
    for chain in why["causes"]:
        assert chain[0] == pu and all(a > b for a, b in zip(chain, chain[1:]))
    assert any(len(chain) == 3 for chain in why["causes"]), "cl-1 was itself caused by the incident"


def test_why_entity_unknown(db, scenario):
    from gnomon.audit.runner import why_entity

    assert why_entity(db.select_graph(scenario.key), "svc:nope") is None


def test_cli(db, redis_raw, falkordb_container, scenario, scratch, capsys):
    from gnomon import cli

    url = falkordb_container.url
    assert cli.main(["audit", "--url", url, "--graph", scenario.key]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert cli.main(["why", "--url", url, "--graph", scenario.key, "--uid", "msg:m1"]) == 0
    assert json.loads(capsys.readouterr().out)["rule"] == "PostUpdate"
    assert cli.main(["why", "--url", url, "--graph", scenario.key, "--uid", "svc:nope"]) == 1
    capsys.readouterr()

    target = f"{scenario.key}:replay:a07-cli"
    assert cli.main(["replay", "--url", url, "--graph", scenario.key, "--target", target]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["key"], out["seq"], out["hash"]) == (target, scenario.head_seq, scenario.head_hash)
    redis_raw.execute_command("GRAPH.DELETE", target)

    db.select_graph(scratch).query("MATCH (t:Task {id: 't2'}) SET t.result = 'forged'")
    assert cli.main(["audit", "--url", url, "--graph", scratch, "--checks", "K4,K6"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert (out["ok"], out["failed"]) == (False, ["K4"])

    db.select_graph(scratch).query("MATCH (r:Rewrite {id: 'co-2'}) SET r.pt = 1")
    assert cli.main(["replay", "--url", url, "--graph", scratch]) == 1
    assert json.loads(capsys.readouterr().out)["divergence"]["reason"] == "error"
