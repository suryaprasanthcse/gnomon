"""For every registered rule, the parser's reads and writes match the declarations in registry.yaml (§7.4, L4)."""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g2a]


def _rules():
    from gnomon.kernel.registry import load_registry

    return load_registry().rules()


@pytest.mark.parametrize("name", [r.name for r in _rules()])
def test_parser_read_write_sets(name):
    from gnomon.footprint.parser import extract
    from gnomon.kernel.registry import load_registry

    rule = load_registry().get(name)
    f = extract(rule.body)
    assert sorted(f.reads) == sorted(rule.reads), f"{name} reads"
    assert sorted(f.writes) == sorted(rule.writes), f"{name} writes"


def test_declared_lists_are_sorted_and_unique():
    for rule in _rules():
        assert list(rule.reads) == sorted(set(rule.reads)), rule.name
        assert list(rule.writes) == sorted(set(rule.writes)), rule.name


def test_guard_reads_of_deactivate_rule():
    from gnomon.footprint.parser import extract
    from gnomon.kernel.registry import load_registry

    f = extract(load_registry().get("DeactivateRule").body)
    assert f.guard_reads == {"Rule", "Rule.name", "Rule.version", "Rule.active", "Rule.class"}
    assert f.writes == {"Rule.active", "Rule.deactivated_seq"}


def test_guard_reads_of_retract_fact():
    from gnomon.footprint.parser import extract
    from gnomon.kernel.registry import load_registry

    f = extract(load_registry().get("RetractFact.OWNED_BY").body)
    assert {"Entity", "Entity.uid", "OWNED_BY", "OWNED_BY.fid", "OWNED_BY.te", "OWNED_BY.vt", "OWNED_BY.vf",
            "OWNER_NOW", "OWNER_NOW.fid"} <= f.guard_reads
    assert "OWNED_BY.tt" not in f.guard_reads  # only written
    assert f.writes == {"OWNED_BY", "OWNED_BY.te", "OWNED_BY.tt", "OWNER_NOW"}


def test_guard_reads_of_assert_fact_supersede_shape():
    from gnomon.footprint.parser import extract
    from gnomon.kernel.registry import load_registry

    f = extract(load_registry().get("AssertFact.OWNED_BY").body)
    assert {"Service", "Team", "OWNED_BY.te", "OWNED_BY.vt", "OWNED_BY.vf", "OWNER_NOW"} <= f.guard_reads
    assert f.writes == {"OWNED_BY", "OWNED_BY.te", "OWNED_BY.tt", "OWNER_NOW"}
