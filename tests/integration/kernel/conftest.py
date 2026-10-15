"""Kernel integration helpers (A03): reference chain hashing and graph snapshots, independent of gnomon code."""

from __future__ import annotations

import hashlib

import pytest

ZERO64 = "0" * 64


def _enc(fields: list) -> bytes:
    out = []
    for x in fields:
        if x is None:
            out.append("~,")
        else:
            text = str(x)
            out.append(f"{len(text.encode('utf-8'))}:{text},")
    return "".join(out).encode("utf-8")


def _digest(fields: list) -> str:
    return hashlib.sha256(_enc(fields)).hexdigest()


def _chain(r: dict) -> str:
    """master §8.5 over a stored Rewrite's properties."""
    return _digest(["rw/1", r["prev"], r["id"], r["seq"], r["pt"], r["lc"], r["rule_sha"], r["agent"], r["params"],
                    r["read_seq"], _digest(list(r["view_keys"]))])


@pytest.fixture(scope="session")
def ref_digest():
    return _digest


@pytest.fixture(scope="session")
def ref_chain():
    return _chain


def rewrites(graph) -> list[dict]:
    rows = graph.query("MATCH (r:Rewrite) RETURN properties(r) AS r ORDER BY r.seq").result_set
    return [row[0] for row in rows]


def snapshot(graph) -> tuple:
    """Everything a rejected proposal must leave untouched."""
    head = graph.query("MATCH (h:Head) RETURN h.seq, h.pt, h.lc, h.hash").result_set
    nodes = graph.query("MATCH (n) RETURN count(n)").result_set[0][0]
    edges = graph.query("MATCH ()-[e]->() RETURN count(e)").result_set[0][0]
    kvs = graph.query("MATCH (k:KeyVersion) RETURN k.key, k.seq, k.rw ORDER BY k.key").result_set
    return head, nodes, edges, kvs


@pytest.fixture(scope="session")
def chain_of():
    return rewrites


@pytest.fixture(scope="session")
def snap():
    return snapshot
