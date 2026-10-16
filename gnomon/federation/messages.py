"""Message shapes (master §17.3): which fields each kind carries and which of them its digest covers, in order.

A message is the XMsg node in the sender's graph. The relay builds `$m` from it: the envelope, the kind's own fields,
and `fields`, the list the receiver's P2 hashes with Kernel.digest. Nothing else of the sender's graph travels: a
property that is not named here (a note poked onto the node, say) never reaches the receiver.
"""

from __future__ import annotations

from collections.abc import Mapping

ENVELOPE = ("id", "chan", "chan_seq", "kind", "digest", "s_pt", "s_lc", "origin_tenant", "origin_seq", "origin_rw")
KIND_FIELDS = {
    "notice": ("incident_ref", "body"),
    "health": ("svc", "health", "mode"),
    "subscribe": ("consumer_svc", "provider_svc", "hard", "criticality"),
    "unsubscribe": ("consumer_svc", "provider_svc"),
}
# After (kind, origin_tenant, id, chan_seq, s_pt, s_lc) each kind's digest covers these, in this order (§17.3).
DIGEST_HEAD = ("kind", "origin_tenant", "id", "chan_seq", "s_pt", "s_lc")
DIGEST_TAIL = KIND_FIELDS


class UnknownKind(ValueError):
    """A message whose kind is not one of the four federation kinds."""


def _digest_value(name: str, value: object) -> object:
    if name == "hard":  # the rules digest toString(hard)
        return "true" if value is True else "false"
    return value


def digest_fields(props: Mapping) -> list:
    """The list Kernel.digest hashes for this message: head fields then the kind's tail, a missing value as null."""
    kind = props.get("kind")
    if kind not in DIGEST_TAIL:
        raise UnknownKind(f"unknown message kind {kind!r}")
    return [props.get(name) for name in DIGEST_HEAD] + [_digest_value(name, props.get(name)) for name in DIGEST_TAIL[kind]]


def message(props: Mapping) -> dict:
    """`$m` for ReceiveMessage.<kind> from the sender's XMsg properties."""
    kind = props.get("kind")
    if kind not in KIND_FIELDS:
        raise UnknownKind(f"unknown message kind {kind!r}")
    m = {name: props.get(name) for name in (*ENVELOPE, *KIND_FIELDS[kind])}
    m["fields"] = digest_fields(props)
    return m
