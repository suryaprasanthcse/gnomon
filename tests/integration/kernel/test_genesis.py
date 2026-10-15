"""R0 Genesis (master §2.5, §6.3, §9.1): the kernel graph it creates, and every PG validation it refuses."""

import json

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1b]

ZERO64 = "0" * 64
EPOCH = 1_791_000_000_000
FACT_TYPES = {
    "OWNED_BY": (["Service"], ["Team"], "functional", "OWNER_NOW", []),
    "DEPENDS_ON": (["Service"], ["Service", "ExternalService"], "set", "DEP_NOW", ["hard"]),
    "DEPLOYED": (["Service"], ["Deploy"], "functional", None, []),
    "REPLICA_OF": (["Service"], ["Service"], "functional", None, []),
    "MEMBER_OF": (["Agent"], ["Team"], "set", None, []),
    "ROOT_CAUSE": (["Incident"], ["Deploy", "Service"], "set", None, ["by", "confidence"]),
    "CONSUMES": (["Consumer"], ["Service"], "set", "CONSUMES_NOW", ["hard"]),
}


def rows(handle, cypher, **params):
    return handle.read(cypher, params)


def test_genesis_creates_kernel(genesis, ref_chain, chain_of):
    from gnomon.bootstrap import ddl
    from gnomon.kernel import composer

    reg = genesis.registry
    head = genesis.head()
    assert head["seq"] == 1
    assert (head["pt"], head["lc"]) == (EPOCH, 0)
    assert head["tenant"] == "test"
    assert head["kernel_sha"] == composer.kernel_sha()

    [r0] = chain_of(genesis.graph)
    assert (r0["id"], r0["seq"], r0["rule"], r0["agent"], r0["read_seq"]) == ("genesis", 1, "Genesis", "kernel", 0)
    assert r0["rule_sha"] == reg.get("Genesis").sha
    assert r0["prev"] == ZERO64
    assert r0["hash"] == ref_chain(r0) == head["hash"]
    assert json.loads(r0["params"])["kernel_sha"] == composer.kernel_sha()

    rules = rows(genesis, "MATCH (r:Rule) RETURN r.name, r.version, r.sha, r.active, r.registered_seq, r.rw, r.caller "
                          "ORDER BY r.name")
    assert rules == [
        [r.name, r.version, r.sha, True, 1, "genesis", list(r.caller)]
        for r in sorted(reg.rules(), key=lambda r: r.name)
    ]
    body = rows(genesis, "MATCH (r:Rule {name: 'Noop'}) RETURN r.body, r.prelude, r.guard_probes_json")[0]
    assert body == [reg.get("Noop").body, "P0", reg.get("Noop").to_param()["guard_probes_json"]]

    fts = rows(genesis, "MATCH (f:FactType) RETURN f.rel, f.s_labels, f.o_labels, f.cardinality, f.projection, "
                        "f.attrs ORDER BY f.rel")
    assert {r[0]: tuple(r[1:]) for r in fts} == FACT_TYPES

    assert rows(genesis, "MATCH (s:Skill:Entity) RETURN s.uid, s.id ORDER BY s.id") == [
        ["skill:ops", "ops"], ["skill:triage", "triage"],
    ]
    agents = rows(genesis, "MATCH (a:Agent:Entity) OPTIONAL MATCH (a)-[:HAS_SKILL]->(s:Skill) "
                           "WITH a, s ORDER BY s.id RETURN a.id, a.uid, a.role, a.active, collect(s.id) ORDER BY a.id")
    assert agents == [
        ["kernel", "agent:kernel", "kernel", True, []],
        ["ops-1", "agent:ops-1", "operator", True, ["ops", "triage"]],
        ["triage-1", "agent:triage-1", "triage", True, ["triage"]],
    ]
    assert rows(genesis, "MATCH (s:Skill) RETURN count(s)") == [[2]]  # HAS_SKILL never duplicates a skill

    ix = rows(genesis, "MATCH (i:IndexEntry) RETURN i.key, i.entity, i.label, i.prop, i.state, i.origin, i.op, "
                       "i.requested_seq, i.settled_seq, i.settled_pt, i.rw")
    assert sorted(ix) == sorted(
        [i.key, i.entity, i.label, i.prop, "operational", "genesis", "create", 1, 1, EPOCH, "genesis"]
        for i in ddl.BASE_INDEXES
    )
    cx = rows(genesis, "MATCH (c:ConstraintEntry) RETURN c.key, c.kind, c.entity, c.label, c.props, c.state")
    assert sorted(cx) == sorted(
        [c.key, c.kind, c.entity, c.label, list(c.props), "operational"] for c in ddl.BASE_CONSTRAINTS
    )

    kvs = rows(genesis, "MATCH (k:KeyVersion) RETURN k.key, k.seq, k.rw ORDER BY k.key")
    # Rule:<name> for every registered rule (DECISIONS D-14: derived from the registry, not fixed to A03's set)
    rule_keys = [f"Rule:{r.name}" for r in reg.rules()]
    expected_keys = sorted({"Head", "Rule:*", "Agent:*", "Skill:*", "FactType:*", "Index:*", *rule_keys})
    assert kvs == [[k, 1, "genesis"] for k in expected_keys]
    assert r0["write_keys"] == ["Head", "Rule:*", *rule_keys, "Agent:*", "Skill:*", "FactType:*", "Index:*"]


@pytest.fixture
def fresh(db, redis_raw, tenant, genesis_config):
    return lambda **changes: _fresh_genesis(db, redis_raw, tenant, genesis_config, **changes)


def _fresh_genesis(db, redis_raw, tenant, cfg, **changes):
    """Genesis on an empty graph (no DDL: PG validation does not need it), with params edited by `changes`."""
    from gnomon.bootstrap.genesis import build_params, run_genesis
    from gnomon.kernel.service import KernelService
    from gnomon.udf import loader

    loader.load(redis_raw)
    registry = cfg["registry"]
    params = build_params(registry, tenant=cfg["tenant"], agents=changes.pop("agents", cfg["agents"]),
                          skills=changes.pop("skills", cfg["skills"]))
    for edit in changes.pop("edits", []):
        edit(params)
    graph = db.select_graph(tenant)
    return run_genesis(KernelService(graph, registry), params, pid="genesis"), graph


def _no_kernel_written(graph):
    assert graph.query("MATCH (n) RETURN count(n)").result_set == [[0]]


def test_genesis_twice_rejected(genesis, snap, genesis_config):
    from gnomon.bootstrap.genesis import build_params, run_genesis

    before = snap(genesis.graph)
    params = build_params(genesis.registry, tenant="test", agents=genesis_config["agents"],
                          skills=genesis_config["skills"])
    outcome = run_genesis(genesis.service, params, pid="genesis-2")
    assert outcome.code == "REJECTED_GUARD"
    assert outcome.detail["guard"] == "no_head"
    assert snap(genesis.graph) == before


def test_fresh_genesis_applies(fresh):
    outcome, graph = fresh()
    assert outcome.code == "APPLIED" and outcome.seq == 1
    assert graph.query("MATCH (h:Head) RETURN h.seq, h.hash").result_set == [[1, outcome.hash]]


def test_genesis_requires_kernel_agent(fresh):
    outcome, graph = fresh(agents=[{"id": "ops-1", "role": "operator", "skills": []}])
    assert (outcome.code, outcome.detail["guard"]) == ("REJECTED_GUARD", "kernel_agent")
    _no_kernel_written(graph)


def test_genesis_requires_an_agent(fresh):
    outcome, graph = fresh(agents=[])
    assert (outcome.code, outcome.detail["guard"]) == ("REJECTED_GUARD", "has_agents")
    _no_kernel_written(graph)


def test_genesis_rule_sha_mismatch(fresh):
    def tamper_noop(params):
        noop = next(r for r in params["rules"] if r["name"] == "Noop")
        noop["sha"] = "f" * 64

    outcome, graph = fresh(edits=[tamper_noop])
    assert (outcome.code, outcome.detail["guard"]) == ("REJECTED_GUARD", "rule_shas_match")
    _no_kernel_written(graph)


def test_genesis_rule_def_tampered(fresh):
    def tamper_def(params):
        noop = next(r for r in params["rules"] if r["name"] == "Noop")
        noop["def_json"] = noop["def_json"].replace('"caller":["kernel"]', '"caller":["kernel","triage"]')

    outcome, graph = fresh(edits=[tamper_def])
    assert (outcome.code, outcome.detail["guard"]) == ("REJECTED_GUARD", "rule_shas_match")
    _no_kernel_written(graph)


def test_genesis_rule_not_pinned(fresh):
    def tamper_genesis(params):
        params["rules"] = [r for r in params["rules"] if r["name"] != "Genesis"]

    outcome, graph = fresh(edits=[tamper_genesis])
    assert (outcome.code, outcome.detail["guard"]) == ("REJECTED_GUARD", "genesis_rule_pinned")
    _no_kernel_written(graph)


def test_genesis_kernel_sha_mismatch(fresh):
    def tamper_src(params):
        params["kernel_src"] = params["kernel_src"] + "\n// edited"

    outcome, graph = fresh(edits=[tamper_src])
    assert (outcome.code, outcome.detail["guard"]) == ("REJECTED_GUARD", "kernel_sha_matches")
    _no_kernel_written(graph)


def test_genesis_agent_unknown_skill_rejected(fresh):
    agents = [{"id": "kernel", "role": "kernel", "skills": []}, {"id": "x-1", "role": "triage", "skills": ["juggling"]}]
    outcome, graph = fresh(agents=agents)
    assert (outcome.code, outcome.detail["guard"]) == ("REJECTED_GUARD", "agent_skills_known")
    _no_kernel_written(graph)
