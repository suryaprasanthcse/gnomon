"""A01 bookkeeping: the digest is pinned everywhere, every spike and fact has a test and a record,
and every FAIL has a DECISIONS entry proposing its fallback."""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]

ROOT = Path(__file__).resolve().parents[2]
SPIKES = [f"S{n}" for n in (*range(1, 9), *range(10, 23))]  # master §23.2 has no S9
FACTS_IDS = [f"F{n}" for n in range(1, 8)]


def test_image_digest_is_pinned_everywhere(facts):
    assert facts.image, "first line of .agent/FACTS.md must be falkordb_image: falkordb/falkordb@sha256:<digest>"
    assert f'FALKORDB_IMAGE = "{facts.image}"' in (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert f"image: {facts.image}" in (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def test_every_spike_and_fact_has_a_test_and_a_record(facts):
    for fact_id in SPIKES + FACTS_IDS:
        prefix = f"test_{fact_id[0].lower()}{int(fact_id[1:]):02d}_"
        assert list((ROOT / "tests" / "spikes").glob(prefix + "*.py")), f"no spike test file for {fact_id}"
    missing = [i for i in SPIKES + FACTS_IDS if i not in facts.entries]
    assert missing == [], f"not recorded in .agent/FACTS.md: {missing}"


def test_every_fail_has_a_fallback_decision(facts):
    decisions = (ROOT / ".agent" / "DECISIONS.md").read_text(encoding="utf-8")
    failed = [i for i, (verdict, _) in facts.entries.items() if verdict == "FAIL"]
    for fact_id in failed:
        assert re.search(rf"^D-\d+ \| [^|]+ \| A01 \| {fact_id}\b", decisions, re.M), f"no DECISIONS entry for {fact_id}"
