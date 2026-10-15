"""Kernel UDFs inside write queries (platform facts S1, S4): values persist, a throw writes nothing.

Per S1 a UDF map cannot be stored as a property, so the HLC {pt, lc} is stored as two integers.
"""

import hashlib

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1a]

W = 1791000000000
ZERO64 = "0" * 64


def test_udf_value_in_set(kq):
    kq("CREATE (n:N) SET n.h = Kernel.sha256('x') RETURN count(n)")
    assert kq("MATCH (n:N) RETURN n.h") == [hashlib.sha256(b"x").hexdigest()]


def test_tick_fields_stored_separately(kq):
    kq("CREATE (h:Head {pt: $pt, lc: 4}) RETURN count(h)", pt=W)
    kq(
        "MATCH (h:Head) WITH h, Kernel.tick(h.pt, h.lc, $wall, 'live', null, null) AS c "
        "SET h.pt = c.pt, h.lc = c.lc RETURN h.pt",
        wall=W,
    )
    assert kq("MATCH (h:Head) RETURN h.pt, h.lc") == [W, 5]


def test_chain_written_in_the_same_query_as_tick(kq, ref_digest):
    kq("CREATE (h:Head {pt: $pt, lc: 0, seq: 0, hash: $zero}) RETURN count(h)", pt=W, zero=ZERO64)
    kq(
        "MATCH (h:Head) WITH h, Kernel.tick(h.pt, h.lc, $wall, 'live', null, null) AS c "
        "WITH h, c, Kernel.chain(h.hash, 'pid-1', h.seq + 1, c.pt, c.lc, $sha, 'agent:kernel', '{}', h.seq, $zero) AS nh "
        "CREATE (r:Rewrite {id: 'pid-1', seq: h.seq + 1, pt: c.pt, lc: c.lc, hash: nh}) "
        "SET h.seq = h.seq + 1, h.pt = c.pt, h.lc = c.lc, h.hash = nh RETURN nh",
        wall=W + 3, sha="a" * 64, zero=ZERO64,
    )
    expected = ref_digest(["rw/1", ZERO64, "pid-1", 1, W + 3, 0, "a" * 64, "agent:kernel", "{}", 0, ZERO64])
    assert kq("MATCH (h:Head), (r:Rewrite) RETURN h.hash, r.hash, h.seq, r.pt, r.lc") == [expected, expected, 1, W + 3, 0]


@pytest.mark.parametrize(
    "throwing_call",
    [
        "Kernel.tick(h.pt, h.lc, $wall, 'bogus', null, null).pt",
        "Kernel.tick(h.pt, h.lc, null, 'replay', h.pt, h.lc).pt",
        "Kernel.digest([1.5])",
    ],
    ids=["bad-mode", "hlc-regression", "digest-bad-field"],
)
def test_throw_rolls_back_the_whole_query(kq, throwing_call):
    kq("CREATE (h:Head {pt: $pt, lc: 4, seq: 7}) RETURN count(h)", pt=W)
    with pytest.raises(Exception, match="BAD_MODE|HLC_REGRESSION|DIGEST_BAD_FIELD"):
        kq(
            f"MATCH (h:Head) CREATE (:Rewrite {{seq: h.seq + 1}}) SET h.seq = h.seq + 1 "
            f"WITH h SET h.x = {throwing_call} RETURN h.seq",
            wall=W,
        )
    assert kq("MATCH (h:Head) OPTIONAL MATCH (r:Rewrite) RETURN h.seq, count(r), h.x") == [7, 0, None]
