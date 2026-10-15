"""Canonical state hash (master §12.2).

Every node and edge becomes one line, keyed by its logical identity rather than its internal id:

    N|<key>|<sorted labels>|<JCS(properties)>
    E|<type>|<key(start)>|<key(end)>|<JCS(properties)>

The lines are sorted, joined with "\n" and hashed with SHA-256. Physical indexes and constraints are not
part of the graph's elements, so they never enter the hash; scratch graphs are separate keys.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

import redis

from gnomon.kernel.jcs import canonicalize

# §12.2, in precedence order: the first label a node carries decides its key property.
KEY_PROPS: tuple[tuple[str, str | None], ...] = (
    ("Entity", "uid"), ("Head", None), ("Rule", "sha"), ("Scorer", "sha"), ("KeyVersion", "key"),
    ("Rewrite", "id"), ("FactType", "rel"), ("Outbox", "pid"), ("Approval", "aid"), ("Decision", "id"),
    ("Candidate", "cid"), ("Evidence", "eid"), ("IndexEntry", "key"), ("ConstraintEntry", "key"),
    ("Channel", "key"), ("XMsg", "id"),
)
HEAD_KEY = "head"


def element_key(labels: Sequence[str], props: Mapping) -> str:
    """A node's logical key; a node with no keyed label is named by its whole property map."""
    for label, prop in KEY_PROPS:
        if label in labels:
            if prop is None:
                return HEAD_KEY
            if props.get(prop) is not None:
                return str(props[prop])
    return "?" + canonicalize(dict(props))


def node_line(labels: Sequence[str], props: Mapping) -> str:
    return f"N|{element_key(labels, props)}|{','.join(sorted(labels))}|{canonicalize(dict(props))}"


def edge_line(etype: str, start_labels: Sequence[str], start_props: Mapping, end_labels: Sequence[str],
              end_props: Mapping, props: Mapping) -> str:
    return (f"E|{etype}|{element_key(start_labels, start_props)}|{element_key(end_labels, end_props)}|"
            f"{canonicalize(dict(props))}")


def canonical_lines(nodes: Iterable[tuple[Sequence[str], Mapping]],
                    edges: Iterable[tuple[str, Sequence[str], Mapping, Sequence[str], Mapping, Mapping]]) -> list[str]:
    """Sorted canonical lines from node rows (labels, props) and edge rows (type, start, end, props)."""
    return sorted([node_line(*n) for n in nodes] + [edge_line(*e) for e in edges])


def hash_lines(lines: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def graph_lines(graph) -> list[str]:
    """The canonical lines of a whole graph; an absent graph has none."""
    try:
        nodes = graph.ro_query("MATCH (n) RETURN labels(n), properties(n)").result_set
    except redis.ResponseError as exc:
        if "empty key" in str(exc):
            return []
        raise
    edges = graph.ro_query("MATCH (a)-[e]->(b) RETURN type(e), labels(a), properties(a), labels(b), properties(b), "
                           "properties(e)").result_set
    return canonical_lines(nodes, edges)


def state_hash(graph) -> str:
    return hash_lines(graph_lines(graph))


def _element(line: str) -> tuple[str, ...]:
    parts = line.split("|")
    return tuple(parts[:2] if parts[0] == "N" else parts[:4])


def first_difference(left: Sequence[str], right: Sequence[str]) -> tuple[str | None, str | None] | None:
    """The first differing element as (left line, right line); None on a side that lacks the element."""
    only_left = sorted((Counter(left) - Counter(right)).elements())
    only_right = sorted((Counter(right) - Counter(left)).elements())
    if not only_left and not only_right:
        return None
    element = _element(min(only_left[:1] + only_right[:1]))
    pick = lambda lines: next((x for x in lines if _element(x) == element), None)  # noqa: E731
    return pick(only_left), pick(only_right)


def differing_properties(left: str | None, right: str | None) -> list[str]:
    """Property names whose values differ between two lines of the same element."""
    if left is None or right is None:
        return []
    a, b = json.loads(left.split("|", 4 if left[0] == "E" else 3)[-1]), \
        json.loads(right.split("|", 4 if right[0] == "E" else 3)[-1])
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
