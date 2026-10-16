"""The decision envelope (master §14.1): schema, sha and the params that bind a proposal to it.

`sha = SHA-256(JCS(envelope without sha))`. The winner's proposal carries `params = winner.params + decision_sha +
attach_sha`, where `attach = JCS(envelope)` is the text sent as `$attach` and `attach_sha = SHA-256(attach)`.
`params_sha = SHA-256(JCS(params without decision_sha and attach_sha))` names the action independent of its proof.
This module builds and checks envelopes only; choosing candidates and scoring them belongs to the Executive (A11).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from gnomon.kernel.jcs import canonicalize

REQUIRED = ("did", "attempt", "aid", "kind", "proposer", "rationale_ref", "scorer", "scorer_sha", "read_seq", "hops",
            "weight_scale", "lambda_n", "lambda_d", "tau", "margin", "baseline", "candidates", "evidence", "cites",
            "footprint_digest", "workload_digest", "channels")
KINDS = ("action", "index")
BINDING = ("decision_sha", "attach_sha")


class EnvelopeError(ValueError):
    """An envelope that is missing a field or holds an invalid one."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _nat(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


CHANNELS = "MATCH (c:Channel) WHERE c.dir = 'in' RETURN c.peer, c.next_seq ORDER BY c.peer"


def channel_positions(graph) -> dict[str, int]:
    """`channels = {peer: Channel(in:peer).next_seq}` of a base graph (§14.1, §18): the inbound channel positions the
    decision was made at. K7 rebuilds the base at `read_seq`, which fixes these too. A tenant without federation has none."""
    return {peer: seq for peer, seq in graph.ro_query(CHANNELS).result_set}


def validate(fields: Mapping) -> None:
    for name in REQUIRED:
        if name not in fields:
            raise EnvelopeError(f"the envelope has no field {name!r}")
    if fields["kind"] not in KINDS:
        raise EnvelopeError(f"kind must be one of {KINDS}")
    if not isinstance(fields["did"], str) or not fields["did"]:
        raise EnvelopeError("did must be a non-empty string")
    if not _nat(fields["attempt"]) or fields["attempt"] < 1:
        raise EnvelopeError("attempt must be an integer >= 1")
    if fields["aid"] != f"{fields['did']}/{fields['attempt']}":
        raise EnvelopeError("aid must be <did>/<attempt>")
    if not _nat(fields["read_seq"]):
        raise EnvelopeError("read_seq must be a non-negative integer")
    channels = fields["channels"]
    if not isinstance(channels, Mapping) or not all(isinstance(p, str) and _nat(n) for p, n in channels.items()):
        raise EnvelopeError("channels must map each peer to a non-negative next_seq")
    for name in ("candidates", "evidence", "cites"):
        if not isinstance(fields[name], list):
            raise EnvelopeError(f"{name} must be a list")
    allowed = {(c["s"], c["fid"]) for c in fields["cites"]}
    for e in fields["evidence"]:
        if not {(c["s"], c["fid"]) for c in e.get("cites", [])} <= allowed:
            raise EnvelopeError("every evidence cite must be one of the envelope's cites")


def seal(fields: Mapping) -> dict:
    """The envelope with its sha. Sealing an already sealed envelope returns it unchanged."""
    body = {k: v for k, v in fields.items() if k != "sha"}
    validate(body)
    return {**body, "sha": _sha(canonicalize(body))}


def verify(envelope: Mapping) -> bool:
    body = {k: v for k, v in envelope.items() if k != "sha"}
    return envelope.get("sha") == _sha(canonicalize(body))


def attach_of(envelope: Mapping) -> str:
    """`$attach`: the canonical envelope including its sha."""
    return canonicalize(dict(envelope))


def params_sha(params: Mapping) -> str:
    return _sha(canonicalize({k: v for k, v in params.items() if k not in BINDING}))


def params_for(winner_params: Mapping, envelope: Mapping) -> dict:
    return {**winner_params, "decision_sha": envelope["sha"], "attach_sha": _sha(attach_of(envelope))}


def pid_of(did: str) -> str:
    return "p-" + _sha(did)[:24]
