"""Dynamic verification (master §7.5): actual writes on a scratch copy ⊆ declared writes and instantiated keys.

Every registered rule is applied in sim mode to a copy of a seeded tenant with generated params. Most
generated proposals are rejected; a rejection must change nothing, and an applied one may only change
elements its rule declares, on instances its write keys name.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

pytestmark = [pytest.mark.integration, pytest.mark.gate_g2a]

EPOCH = 1_791_000_000_000
FUTURE = 4_102_444_800_000
SERVICES = ["svc:a", "svc:b", "svc:c", "svc:zz"]
SUBJECTS = {"MEMBER_OF": ["agent:ops-1", "agent:triage-1", "svc:a"], "ROOT_CAUSE": ["inc:i1", "svc:a"]}
OBJECTS = {
    "OWNED_BY": ["team:t1", "team:t2", "svc:b"], "DEPENDS_ON": ["svc:b", "svc:c", "ext:x1", "svc:a"],
    "DEPLOYED": ["dep:d1", "dep:d2", "team:t1"], "REPLICA_OF": ["svc:a", "svc:c", "svc:b"],
    "MEMBER_OF": ["team:t1", "team:t2"], "ROOT_CAUSE": ["dep:d1"],
}
VF = st.sampled_from([EPOCH + 50, EPOCH + 100, EPOCH + 150, EPOCH + 400, FUTURE])


def assert_fact(rel):
    return st.fixed_dictionaries(
        {"s": st.sampled_from(SUBJECTS.get(rel, SERVICES)), "o": st.sampled_from(OBJECTS[rel]), "vf": VF,
         "supersede": st.booleans()},
        optional={"old_o": st.sampled_from(OBJECTS[rel]), "hard": st.booleans()},
    )


def retract_fact(rel, seed):
    fid = st.sampled_from([seed.fids.get(rel, "none#0"), "none#0"])
    return st.fixed_dictionaries(
        {"s": st.sampled_from(SUBJECTS.get(rel, SERVICES)), "o": st.sampled_from(OBJECTS[rel]), "fid": fid,
         "mode": st.sampled_from(["end", "void", "erase"])},
        optional={"vt_end": VF},
    )


def strategy(name, seed):
    from gnomon.kernel.registry import register_rule_params

    family, _, member = name.partition(".")
    if family == "AssertFact":
        return assert_fact(member)
    if family == "RetractFact":
        return retract_fact(member, seed)
    ids = st.sampled_from(["a", "web", "t1", "new"])
    if name == "RegisterEntity.Service":
        return st.fixed_dictionaries({"id": ids, "name": st.just("n"), "tier": st.just("t"),
                                      "criticality": st.integers(0, 6), "user_facing": st.sampled_from([True, "x"])})
    if name in ("RegisterEntity.Team", "RegisterEntity.Skill"):
        return st.fixed_dictionaries({"id": ids, "name": st.just("n")})
    if name == "RegisterEntity.ExternalService":
        return st.fixed_dictionaries({"id": ids, "tenant": st.just("acme"), "remote_id": st.just("r"),
                                      "contract_criticality": st.integers(0, 6)})
    if name == "RegisterEntity.Deploy":
        return st.fixed_dictionaries({"id": st.sampled_from(["d1", "d9"]), "service": st.sampled_from(["a", "zz"]),
                                      "version": st.just("1"), "verified": st.booleans(),
                                      "expected_health": st.sampled_from(["ok", "melting"])})
    if name == "RegisterRule":
        import dataclasses

        team = seed.registry.get("RegisterEntity.Team")
        candidates = [dataclasses.replace(team, version=2), dataclasses.replace(team, version=3),
                      dataclasses.replace(team, name="RegisterEntity.Squad")]
        return st.sampled_from(candidates).map(register_rule_params)
    if name == "DeactivateRule":
        return st.fixed_dictionaries({"name": st.sampled_from(["AssertFact.MEMBER_OF", "RegisterRule", "Genesis",
                                                               "RetractFact.ROOT_CAUSE"]),
                                      "version": st.sampled_from([1, 2])})
    if name == "RegisterAgent":
        return st.fixed_dictionaries({"op": st.sampled_from(["register", "deactivate"]),
                                      "id": st.sampled_from(["inv-9", "triage-1", "kernel"]),
                                      "role": st.sampled_from(["investigator", "kernel", "wizard"]),
                                      "skills": st.sampled_from([[], ["triage"], ["juggling"]])})
    raise AssertionError(f"no params strategy for {name}")


def _rule_names():
    from gnomon.kernel.registry import load_registry

    return [r.name for r in load_registry().rules() if r.prelude != "PG"]


def _verify(db, redis_raw, seed, name, p, n):
    from gnomon.footprint.verify import verify

    return verify(db, redis_raw, seed.key, seed.scratch(), seed.registry, rule=name, p=p, pid=f"fpv-{n}",
                  agent="ops-1")


@pytest.mark.property
@pytest.mark.parametrize("name", _rule_names())
@settings(max_examples=12, deadline=None)
@given(data=st.data())
def test_dynamic_verification(db, redis_raw, fpv_seed, name, data):
    p = data.draw(strategy(name, fpv_seed), label="p")
    result = _verify(db, redis_raw, fpv_seed, name, p, 0)
    assert result.violations == [], (name, p, result.outcome, result.violations)
    if result.outcome.code != "APPLIED":
        assert result.changes == [], (name, p, result.outcome)


KNOWN_GOOD = {
    "AssertFact.OWNED_BY": {"s": "svc:a", "o": "team:t2", "vf": EPOCH + 200, "supersede": True, "old_o": "team:t1"},
    "AssertFact.DEPENDS_ON": {"s": "svc:a", "o": "ext:x1", "vf": EPOCH + 200, "supersede": False, "hard": False},
    "AssertFact.DEPLOYED": {"s": "svc:a", "o": "dep:d2", "vf": EPOCH + 200, "supersede": True, "old_o": "dep:d1"},
    "AssertFact.REPLICA_OF": {"s": "svc:a", "o": "svc:c", "vf": EPOCH + 200, "supersede": False},
    "AssertFact.MEMBER_OF": {"s": "agent:ops-1", "o": "team:t2", "vf": EPOCH + 200, "supersede": False},
    "RetractFact.OWNED_BY": {"s": "svc:a", "o": "team:t1", "fid": "OWNED_BY", "mode": "end", "vt_end": EPOCH + 300},
    "RetractFact.DEPENDS_ON": {"s": "svc:a", "o": "svc:b", "fid": "DEPENDS_ON", "mode": "void"},
    "RetractFact.DEPLOYED": {"s": "svc:a", "o": "dep:d1", "fid": "DEPLOYED", "mode": "end", "vt_end": EPOCH + 300},
    "RetractFact.REPLICA_OF": {"s": "svc:b", "o": "svc:c", "fid": "REPLICA_OF", "mode": "void"},
    "RetractFact.MEMBER_OF": {"s": "agent:ops-1", "o": "team:t1", "fid": "MEMBER_OF", "mode": "end",
                              "vt_end": EPOCH + 300},
    "RetractFact.ROOT_CAUSE": {"s": "inc:i1", "o": "dep:d1", "fid": "ROOT_CAUSE", "mode": "end",
                               "vt_end": EPOCH + 300},
    "RegisterEntity.Service": {"id": "web", "name": "Web", "tier": "t0", "criticality": 5, "user_facing": True},
    "RegisterEntity.Team": {"id": "sre", "name": "SRE"},
    "RegisterEntity.Skill": {"id": "dba"},
    "RegisterEntity.ExternalService": {"id": "pay", "tenant": "acme", "remote_id": "p", "contract_criticality": 4},
    "RegisterEntity.Deploy": {"id": "d9", "service": "b", "version": "9", "verified": True, "expected_health": "ok"},
    "DeactivateRule": {"name": "AssertFact.MEMBER_OF", "version": 1},
    "RegisterAgent": {"op": "register", "id": "inv-9", "role": "investigator", "skills": ["triage", "ops"]},
}


@pytest.mark.parametrize("name", _rule_names())
def test_each_rule_applies_and_verifies(db, redis_raw, fpv_seed, name):
    import dataclasses

    from gnomon.kernel.registry import register_rule_params

    if name == "RegisterRule":
        p = register_rule_params(dataclasses.replace(fpv_seed.registry.get("RegisterEntity.Team"), version=2))
    else:
        p = dict(KNOWN_GOOD[name])
        if "fid" in p:
            p["fid"] = fpv_seed.fids[p["fid"]]
    result = _verify(db, redis_raw, fpv_seed, name, p, 1)
    assert result.outcome.code == "APPLIED", (name, result.outcome)
    assert result.changes, name
    assert result.violations == []


def test_genesis_verifies_on_an_empty_scratch_graph(db, redis_raw, genesis_config, tenant):
    from gnomon.bootstrap.genesis import build_params
    from gnomon.footprint.verify import verify

    params = build_params(genesis_config["registry"], tenant="fpv", agents=genesis_config["agents"],
                          skills=genesis_config["skills"])
    result = verify(db, redis_raw, None, f"{tenant}:replay:fpv-g", genesis_config["registry"], rule="Genesis",
                    p=params, pid="genesis", agent="kernel")
    assert result.outcome.code == "APPLIED", result.outcome
    assert len(result.changes) > 50
    assert result.violations == []


def test_scratch_graphs_are_deleted(db, redis_raw, fpv_seed):
    _verify(db, redis_raw, fpv_seed, "RegisterEntity.Team", {"id": "zz", "name": "z"}, 2)
    left = [k for k in redis_raw.execute_command("GRAPH.LIST") if k.startswith(fpv_seed.key + ":replay:")]
    assert left == []
