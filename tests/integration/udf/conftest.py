"""Kernel UDF harness (A02): the Kernel library loaded once through gnomon.udf.loader, plus Python references.

The library is loaded with the loader's idempotent load: identical code already on the server is left
alone, so parallel workers never replace a library another worker is calling (platform fact S22).
"""

from __future__ import annotations

import hashlib

import pytest

def _ref_enc(fields: list) -> str:
    """Reference netstring encoding of master §8.2, written independently of udf/kernel.js."""
    out = []
    for x in fields:
        if x is None:
            out.append("~,")
            continue
        if isinstance(x, bool) or not isinstance(x, (int, str)):
            raise TypeError(f"not an encodable field: {x!r}")
        text = str(x)
        out.append(f"{len(text.encode('utf-8'))}:{text},")
    return "".join(out)


def _ref_digest(fields: list) -> str:
    return hashlib.sha256(_ref_enc(fields).encode("utf-8")).hexdigest()


@pytest.fixture(scope="session")
def ref_enc():
    return _ref_enc


@pytest.fixture(scope="session")
def ref_digest():
    return _ref_digest


@pytest.fixture(scope="session")
def kernel_sha(redis_raw) -> str:
    from gnomon.udf import loader

    return loader.load(redis_raw)


@pytest.fixture
def kq(db, tenant, kernel_sha):
    """kq(cypher, **params) runs one query on a throwaway tenant graph and returns its single row."""
    graph = db.select_graph(tenant)

    def _q(cypher: str, **params):
        rows = graph.query(cypher, params).result_set
        assert len(rows) == 1, rows
        return rows[0]

    _q.graph = graph
    return _q
