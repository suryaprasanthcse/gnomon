"""Segment composition (master §6.1): segment order by rule kind, T0 substitution, add-ons not yet installed."""

import dataclasses
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g1b]

ROOT = Path(__file__).resolve().parents[3]
NOOP_YAML = ROOT / "tests" / "fixtures" / "rules" / "noop.yaml"
T0 = "Kernel.tick(h.pt, h.lc, timestamp(), $mode, $rec_pt, $rec_lc)"


@pytest.fixture(scope="module")
def genesis():
    from gnomon.kernel.registry import load_registry

    return load_registry().get("Genesis")


@pytest.fixture(scope="module")
def noop():
    from gnomon.kernel.registry import load_rule_file

    return load_rule_file(NOOP_YAML)


def _in_order(text: str, parts: list[str]) -> bool:
    at = 0
    for part in parts:
        found = text.find(part, at)
        if found < 0:
            return False
        at = found + len(part)
    return True


def test_genesis_is_pg_body_q0_ret(genesis):
    from gnomon.kernel import composer

    assert composer.plan(genesis) == ["PG", "BODY", "Q0", "RET"]
    text = composer.compose(genesis)
    assert _in_order(text, [composer.segment("PG"), genesis.body, composer.segment("Q0"), composer.segment("RET")])
    assert "MATCH (h:Head)\n" not in text.split(genesis.body)[0]  # no P0 before the body


def test_standard_is_p0_body_q0_ret_with_t0(noop):
    from gnomon.kernel import composer

    assert composer.plan(noop) == ["P0", "BODY", "Q0", "RET"]
    text = composer.compose(noop)
    p0 = composer.segment("P0")
    assert "<TICK>" in p0
    assert "<TICK>" not in text
    assert _in_order(text, [p0.split("<TICK>")[0], T0, p0.split("<TICK>")[1], noop.body, composer.segment("Q0"),
                            composer.segment("RET")])
    assert text.count("Kernel.tick(") == 1


def test_every_composed_query_ticks_exactly_once(genesis, noop):
    from gnomon.kernel import composer

    assert composer.compose(genesis).count("Kernel.tick(") == 1
    assert composer.compose(noop).count("Kernel.tick(") == 1


@pytest.mark.parametrize(
    ("change", "segment"),
    [({"requires_decision": True, "evidence": True}, "P1"), ({"prelude": "P2"}, "P2"), ({"prelude": "P3"}, "P3")],
    ids=["decision", "federation-receive", "federation-ack"],
)
def test_add_on_segments_are_not_installed(noop, change, segment):
    from gnomon.kernel import composer

    rule = dataclasses.replace(noop, **change)
    with pytest.raises(composer.SegmentNotInstalled, match=f"segment not installed: {segment}"):
        composer.compose(rule)


def test_e0_is_not_installed_either():
    from gnomon.kernel import composer

    with pytest.raises(composer.SegmentNotInstalled, match="segment not installed: E0"):
        composer.segment("E0")


def test_unknown_prelude_is_refused(noop):
    from gnomon.kernel import composer

    with pytest.raises(ValueError, match="prelude"):
        composer.compose(dataclasses.replace(noop, prelude="P9"))


def test_pg_only_for_genesis(noop):
    from gnomon.kernel import composer

    with pytest.raises(ValueError, match="Genesis"):
        composer.compose(dataclasses.replace(noop, prelude="PG"))


def test_kernel_source_pins_udfs_and_segments():
    import hashlib

    from gnomon.kernel import composer
    from gnomon.udf import loader

    src = composer.kernel_source()
    assert loader.source() in src
    for name in ("PG", "P0", "Q0", "RET"):
        assert composer.segment(name) in src
    assert (ROOT / "gnomon" / "kernel" / "composer.py").read_text(encoding="utf-8") in src
    assert composer.kernel_sha() == hashlib.sha256(src.encode("utf-8")).hexdigest()
    assert composer.kernel_source() == src  # deterministic
