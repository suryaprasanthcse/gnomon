"""§14.1 decision envelope: sha = SHA-256(JCS(decision without sha)); params carry decision_sha and attach_sha."""

import hashlib
import json

import pytest

from gnomon.executive import envelope
from gnomon.kernel.jcs import canonicalize

pytestmark = [pytest.mark.integration, pytest.mark.gate_ta]

FIELDS = {
    "did": "D-7", "attempt": 2, "aid": "D-7/2", "kind": "action", "proposer": "agent:investigator-a",
    "rationale_ref": "llm:9a0f", "scorer": "Exec.blastRadius", "scorer_sha": "e3b7", "read_seq": 57, "hops": 4,
    "weight_scale": 2520, "lambda_n": 3, "lambda_d": 2, "tau": 2520, "margin": 1260,
    "baseline": {"impacted_digest": "x", "teams": ["team:orion"], "region_size": 23},
    "candidates": [{"k": 0, "rule": "EscalateIncident", "params": {"incident": "INC-3"}, "params_sha": "ab",
                    "outcome": "scored", "benefit": 0, "harm": 0, "teams_new": 0, "score_num": 0, "won": True}],
    "evidence": [], "cites": [], "footprint_digest": "fd", "workload_digest": None, "channels": {},
}


def test_envelope_sha():
    d = envelope.seal(FIELDS)
    body = {k: v for k, v in d.items() if k != "sha"}
    assert d["sha"] == hashlib.sha256(canonicalize(body).encode("utf-8")).hexdigest()
    assert envelope.verify(d) and envelope.seal(body) == d  # sealing is idempotent
    assert envelope.seal({**FIELDS, "read_seq": 58})["sha"] != d["sha"]
    assert not envelope.verify({**d, "hops": 5})


def test_attach_is_the_canonical_envelope_with_its_sha():
    d = envelope.seal(FIELDS)
    assert envelope.attach_of(d) == canonicalize(d) and json.loads(envelope.attach_of(d))["sha"] == d["sha"]


def test_params_carry_decision_sha_and_attach_sha():
    d = envelope.seal(FIELDS)
    winner = {"incident": "INC-3", "svc": "payments-api"}
    p = envelope.params_for(winner, d)
    assert p == {**winner, "decision_sha": d["sha"],
                 "attach_sha": hashlib.sha256(envelope.attach_of(d).encode("utf-8")).hexdigest()}
    assert envelope.params_sha(p) == envelope.params_sha(winner)  # the two sha fields are excluded
    assert envelope.params_sha(winner) == hashlib.sha256(canonicalize(winner).encode("utf-8")).hexdigest()


def test_pid_is_derived_from_the_did():
    assert envelope.pid_of("D-7") == "p-" + hashlib.sha256(b"D-7").hexdigest()[:24]


@pytest.mark.parametrize("missing", sorted(FIELDS))
def test_every_field_is_required(missing):
    broken = {k: v for k, v in FIELDS.items() if k != missing}
    with pytest.raises(envelope.EnvelopeError, match=missing):
        envelope.seal(broken)


@pytest.mark.parametrize("change", [{"kind": "other"}, {"attempt": 0}, {"read_seq": -1}, {"aid": "D-7/9"},
                                    {"candidates": "none"}])
def test_invalid_values_are_refused(change):
    with pytest.raises(envelope.EnvelopeError):
        envelope.seal({**FIELDS, **change})
