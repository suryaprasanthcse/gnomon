"""Rule definitions (master §5.1): sha = SHA-256(JCS(def_json)); every hashed field changes the sha."""

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g1b]

ROOT = Path(__file__).resolve().parents[3]
NOOP_YAML = ROOT / "tests" / "fixtures" / "rules" / "noop.yaml"
DEF_FIELDS = {
    "name", "version", "class", "layer", "family", "caller", "prelude", "requires_decision", "gated", "evidence",
    "reads", "writes", "key_templates", "view_templates", "guard_probes", "body",
}


@pytest.fixture(scope="module")
def registry():
    from gnomon.kernel.registry import load_registry

    return load_registry()


@pytest.fixture(scope="module")
def noop():
    from gnomon.kernel.registry import load_rule_file

    return load_rule_file(NOOP_YAML)


def test_sha_is_sha256_of_jcs_def_json(registry):
    from gnomon.kernel.jcs import canonicalize

    genesis = registry.get("Genesis")
    as_dict = json.loads(genesis.def_json)
    assert set(as_dict) == DEF_FIELDS
    assert genesis.def_json == canonicalize(as_dict)
    assert genesis.sha == hashlib.sha256(genesis.def_json.encode("utf-8")).hexdigest()


def test_registry_holds_genesis_only(registry):
    # A04 registers the core rules at genesis too (DECISIONS D-14): Genesis comes first.
    assert registry.rules()[0].name == "Genesis"
    assert sum(r.prelude == "PG" for r in registry.rules()) == 1
    genesis = registry.get("Genesis")
    assert (genesis.version, genesis.cls, genesis.layer, genesis.prelude) == (1, "kernel", "core", "PG")
    assert genesis.caller == ("kernel",)
    assert genesis.key_templates == (
        "Head", "Rule:*", "Rule:{p.rules[].name}", "Agent:*", "Skill:*", "FactType:*", "Index:*",
    )
    assert [g["name"] for g in genesis.guard_probes] == [
        "no_head", "genesis_rule_pinned", "kernel_sha_matches", "has_agents", "kernel_agent",
        "agent_skills_known", "rule_shas_match",
    ]


def test_unknown_rule_raises(registry):
    with pytest.raises(KeyError, match="NoSuchRule"):
        registry.get("NoSuchRule")


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2}, {"cls": "memory"}, {"layer": "T-A"}, {"family": "Fam"}, {"caller": ("kernel", "operator")},
        {"prelude": "P2"}, {"requires_decision": True}, {"gated": True}, {"evidence": True},
        {"reads": ("Task.state",)}, {"writes": ("Noop", "Blocker")}, {"key_templates": ("Noop:x",)},
        {"view_templates": ("Noop:*",)}, {"guard_probes": ({"name": "g", "cypher": "RETURN 1"},)},
        {"body": "WITH h, causes, nseq, now, now_lc\n"}, {"name": "Noop2"},
    ],
    ids=lambda c: next(iter(c)),
)
def test_changed_field_changes_sha(noop, change):
    changed = dataclasses.replace(noop, **change)
    assert changed.sha != noop.sha
    assert changed.def_json != noop.def_json


def test_caller_is_sorted_in_def_json(noop):
    shuffled = dataclasses.replace(noop, caller=("operator", "kernel"))
    in_order = dataclasses.replace(noop, caller=("kernel", "operator"))
    assert json.loads(shuffled.def_json)["caller"] == ["kernel", "operator"]
    assert shuffled.sha == in_order.sha


def test_sha_is_stable_across_loads(noop):
    from gnomon.kernel.registry import load_rule_file

    assert load_rule_file(NOOP_YAML).sha == noop.sha


def test_rule_param_carries_def_json_and_sha(noop):
    param = noop.to_param()
    assert param["def_json"] == noop.def_json
    assert param["sha"] == noop.sha
    assert param["name"] == "Noop" and param["class"] == "kernel"
    from gnomon.kernel.jcs import canonicalize

    assert param["guard_probes_json"] == canonicalize(list(noop.guard_probes))
    assert param["lint_sha"] is None


def test_family_placeholders_are_substituted_before_hashing(tmp_path):
    from gnomon.kernel.registry import expand_family, load_rule_file

    (tmp_path / "f.cypher").write_text("MATCH (s:<S>)-[f:<REL>]->(o:<O>)\nWITH KCTX\n", encoding="utf-8")
    (tmp_path / "f.yaml").write_text(
        "name: AssertFact\nversion: 1\nclass: memory\nlayer: core\nfamily: AssertFact\ncaller: [ingest]\n"
        "prelude: P0\nkey_templates: ['<REL>:*']\nbody_file: f.cypher\n",
        encoding="utf-8",
    )
    template = load_rule_file(tmp_path / "f.yaml")
    member = expand_family(template, "OWNED_BY", {"<REL>": "OWNED_BY", "<S>": "Service", "<O>": "Team"})
    assert member.name == "AssertFact.OWNED_BY"
    assert member.family == "AssertFact"
    assert member.body == "MATCH (s:Service)-[f:OWNED_BY]->(o:Team)\nWITH h, causes, nseq, now, now_lc\n"
    assert member.key_templates == ("OWNED_BY:*",)
    assert member.sha != template.sha
