"""Harness self-tests: marker registry (RUNBOOK_A §4.3), hypothesis profiles (§4.4), injected clock."""

from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import settings

pytestmark = [pytest.mark.unit, pytest.mark.gate_h0]

ROOT = Path(__file__).resolve().parents[2]

LAYER_MARKERS = [
    "unit", "integration", "property", "stateful", "concurrency",
    "mutation", "e2e", "chaos", "spike",
]
GATE_MARKERS = [
    "gate_h0", "gate_g0", "gate_g1a", "gate_g1b", "gate_g1c", "gate_g2a", "gate_g2b",
    "gate_g3", "gate_g4a", "gate_g4b", "gate_ta", "gate_tb", "gate_tc", "gate_td",
    "gate_te", "gate_tf", "gate_assure", "gate_ship",
]


def _registered(pytestconfig) -> set[str]:
    return {line.split(":", 1)[0].strip() for line in pytestconfig.getini("markers")}


def test_markers_registered(pytestconfig, pytester):
    registered = _registered(pytestconfig)
    missing = [m for m in LAYER_MARKERS + GATE_MARKERS if m not in registered]
    assert missing == []

    # Run the project's real pytest configuration against an unknown and a known marker.
    pytester.makepyprojecttoml((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytester.makepyfile(
        test_unknown="""
        import pytest

        @pytest.mark.not_a_registered_marker
        def test_x():
            assert True
        """,
        test_known="""
        import pytest

        @pytest.mark.gate_h0
        def test_y():
            assert True
        """,
    )
    unknown = pytester.runpytest("test_unknown.py")
    assert unknown.ret != 0
    unknown.stdout.fnmatch_lines(["*not_a_registered_marker*not found in `markers`*"])
    known = pytester.runpytest("test_known.py")
    assert known.ret == 0
    known.assert_outcomes(passed=1)


def test_hypothesis_profiles():
    ci = settings.get_profile("ci")
    assert ci.max_examples == 200
    assert ci.derandomize is True
    assert ci.deadline == timedelta(seconds=2)

    nightly = settings.get_profile("nightly")
    assert nightly.max_examples == 2000
    assert nightly.derandomize is False
    assert nightly.deadline is None


def test_clock_is_deterministic_and_steppable(clock):
    start = clock()
    assert clock() == start  # reading does not advance time
    clock.step(250)
    assert clock() == start + 250
    with pytest.raises(ValueError):
        clock.step(-1)


def test_deferred_fixtures_fail_with_module_name(request):
    with pytest.raises(RuntimeError, match="A03"):
        request.getfixturevalue("genesis")
    with pytest.raises(RuntimeError, match="A09"):
        request.getfixturevalue("recorded_llm")
