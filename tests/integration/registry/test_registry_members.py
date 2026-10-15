"""Registry contents (master §5.2, §9.0, §9.3, §9.4, §9.16): family members expanded, hashed and pinned at genesis."""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g1c]

ASSERT = {"OWNED_BY": "FP", "DEPENDS_ON": "SP", "DEPLOYED": "F", "REPLICA_OF": "F", "MEMBER_OF": "S"}
RETRACT = ["OWNED_BY", "DEPENDS_ON", "DEPLOYED", "REPLICA_OF", "MEMBER_OF", "ROOT_CAUSE"]
ENTITIES = ["Service", "Team", "Skill", "ExternalService", "Deploy"]
CORE = (
    {"Genesis", "RegisterRule", "DeactivateRule", "RegisterAgent"}
    | {f"AssertFact.{r}" for r in ASSERT} | {f"RetractFact.{r}" for r in RETRACT}
    | {f"RegisterEntity.{e}" for e in ENTITIES}
)
FACT_CALLERS = ("ingest", "investigator", "operator")


@pytest.fixture(scope="module")
def registry():
    from gnomon.kernel.registry import load_registry

    return load_registry()


def test_core_rule_set(registry):
    # A06 and later register more core rules (DECISIONS D-26): the A04 set must be present, each name once.
    names = [r.name for r in registry.rules()]
    assert names[0] == "Genesis"
    assert CORE <= set(names) and len(names) == len(set(names))


def test_every_rule_is_expanded_and_hashed(registry):
    for rule in registry.rules():
        for token in ("<REL>", "<SPRED>", "<OPRED>", "<PROJ>", "<EXTRA>", "<ATTRS>", "<ATTR_COPY>", "<Label>",
                      "<KIND>", "<VALID>", "<PROPS>", "KCTX", "LIVE("):
            assert token not in rule.body, (rule.name, token)
            assert all(token not in g["cypher"] for g in rule.guard_probes), (rule.name, token)
            assert all(token not in k for k in rule.key_templates), (rule.name, token)
    assert len({r.sha for r in registry.rules()}) == len(registry.rules())  # D-26


@pytest.mark.parametrize("rel", sorted(ASSERT))
def test_assert_members(registry, rel):
    rule = registry.get(f"AssertFact.{rel}")
    assert (rule.cls, rule.layer, rule.family, rule.prelude, tuple(sorted(rule.caller))) == (
        "memory", "core", "AssertFact", "P0", FACT_CALLERS)
    assert rule.key_templates == (f"{rel}:{{p.s}}:*", f"{rel}:*:{{p.o}}", f"{rel}:*", f"{rel}:*:{{p.old_o}}")
    assert [g["name"] for g in rule.guard_probes] == ["entities_exist_and_typed", "vf_not_future", "extra",
                                                      "cardinality_ok"]
    shape = ASSERT[rel]
    projection = {"OWNED_BY": "OWNER_NOW", "DEPENDS_ON": "DEP_NOW"}.get(rel)
    assert (f":{projection}" in rule.body) == bool(projection)
    assert ("supersede" in rule.body) == shape.startswith("F")
    assert f"[:{rel} " in rule.body


@pytest.mark.parametrize("rel", RETRACT)
def test_retract_members(registry, rel):
    rule = registry.get(f"RetractFact.{rel}")
    assert (rule.cls, rule.family, tuple(sorted(rule.caller))) == ("memory", "RetractFact", FACT_CALLERS)
    assert rule.key_templates == (f"{rel}:{{p.s}}:*", f"{rel}:*:{{p.o}}", f"{rel}:*")
    assert [g["name"] for g in rule.guard_probes] == ["version_current", "mode_valid"]


@pytest.mark.parametrize("label", ENTITIES)
def test_register_entity_members(registry, label):
    rule = registry.get(f"RegisterEntity.{label}")
    assert (rule.cls, tuple(sorted(rule.caller))) == ("meta", ("ingest", "operator"))
    assert rule.key_templates == (f"{label}:*", f"{label}:{{p.id}}:state")
    probes = [g["name"] for g in rule.guard_probes]
    assert probes == (["service_exists", "new_uid", "valid"] if label == "Deploy" else ["new_uid", "valid"])


@pytest.mark.parametrize(
    ("name", "keys", "probes"),
    [
        ("RegisterRule", ("Rule:{p.def.name}", "Rule:*"), ["sha_matches_def", "sha_new", "version_next"]),
        ("DeactivateRule", ("Rule:{p.name}", "Rule:*"), ["active_and_not_kernel"]),
        ("RegisterAgent", ("Agent:*", "Agent:{p.id}:state"), ["op_valid"]),
    ],
)
def test_meta_rules(registry, name, keys, probes):
    rule = registry.get(name)
    assert (rule.cls, rule.caller, rule.prelude) == ("meta", ("operator",), "P0")
    assert rule.key_templates == keys
    assert [g["name"] for g in rule.guard_probes] == probes


def test_fact_types_live_in_the_registry(registry):
    from gnomon.bootstrap.genesis import build_params

    rels = [ft["rel"] for ft in registry.fact_types()]
    assert rels == ["OWNED_BY", "DEPENDS_ON", "DEPLOYED", "REPLICA_OF", "MEMBER_OF", "ROOT_CAUSE", "CONSUMES"]
    params = build_params(registry, tenant="t", agents=[{"id": "kernel", "role": "kernel"}], skills=[])
    assert params["fact_types"] == registry.fact_types()


def test_nested_key_path_and_roles_binding(registry):
    from gnomon.kernel.service import ROLES, Proposal, bind, instantiate_keys

    assert instantiate_keys(["Rule:{p.def.name}"], {"def": {"name": "X"}}, "pid") == ["Rule:X"]
    assert instantiate_keys(["Rule:{p.def.name}"], {"def": None}, "pid") == []
    assert ROLES == ("kernel", "operator", "ingest", "triage", "investigator", "comms", "responder", "human",
                     "scheduler", "dispatcher", "executive", "optimizer", "reconciler", "relay")
    params = bind(registry.get("RegisterAgent"), Proposal(rule="RegisterAgent", p={"op": "deactivate", "id": "a"},
                                                          pid="p", agent="ops-1", read_seq=1))
    assert params["ROLES"] == list(ROLES)
