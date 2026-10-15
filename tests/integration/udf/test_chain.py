"""Kernel.chain (master §8.5): the digest of the "rw/1"-tagged rewrite fields, linking each rewrite to its parent."""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1a]

ZERO64 = "0" * 64
W = 1791000000000
SHA_A = "a" * 64

CHAIN = (
    "RETURN Kernel.chain($prev, $id, $seq, $pt, $lc, $rule_sha, $agent, $params, $read_seq, $vk_digest)"
)
TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"), max_size=80)
NAT = st.integers(min_value=0, max_value=2**53 - 1)


def args(prev=ZERO64, id="pid-1", seq=1, pt=W, lc=0, rule_sha=SHA_A, agent="agent:kernel", params="{}", read_seq=0,
         vk_digest=ZERO64):
    return dict(prev=prev, id=id, seq=seq, pt=pt, lc=lc, rule_sha=rule_sha, agent=agent, params=params,
                read_seq=read_seq, vk_digest=vk_digest)


def tagged(a: dict) -> list:
    return ["rw/1", a["prev"], a["id"], a["seq"], a["pt"], a["lc"], a["rule_sha"], a["agent"], a["params"],
            a["read_seq"], a["vk_digest"]]


def test_chain_is_digest_of_tagged_fields(kq, ref_digest):
    genesis = args()
    assert kq(CHAIN, **genesis) == [ref_digest(tagged(genesis))]


def test_chain_links_successive_rewrites(kq, ref_digest):
    first = args()
    h1 = kq(CHAIN, **first)[0]
    second = args(prev=h1, id="pid-2", seq=2, pt=W + 5, lc=0, params='{"service":"svc-07"}', read_seq=1)
    h2 = kq(CHAIN, **second)[0]
    assert h2 == ref_digest(tagged(second))
    assert h2 != h1


def test_chain_matches_kernel_digest(kq):
    a = args(params='{"note":"é😀"}')
    via_chain = kq(CHAIN, **a)[0]
    via_digest = kq("RETURN Kernel.digest($f)", f=tagged(a))[0]
    assert via_chain == via_digest


@pytest.mark.parametrize("field", ["prev", "id", "seq", "pt", "lc", "rule_sha", "agent", "params", "read_seq", "vk_digest"])
def test_every_field_is_bound(kq, field):
    base = args()
    changed = dict(base)
    changed[field] = base[field] + 1 if isinstance(base[field], int) else base[field] + "x"
    assert kq(CHAIN, **base) != kq(CHAIN, **changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [("seq", "1"), ("pt", 1.5), ("prev", None), ("params", 7), ("read_seq", True), ("vk_digest", ["x"])],
)
def test_rejects_mistyped_fields(kq, field, value):
    bad = args()
    bad[field] = value
    with pytest.raises(Exception, match="CHAIN_BAD_ARG"):
        kq(CHAIN, **bad)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=60)
@given(id=TEXT, seq=NAT, pt=NAT, lc=NAT, agent=TEXT, params=TEXT, read_seq=NAT)
def test_chain_matches_reference(kq, ref_digest, id, seq, pt, lc, agent, params, read_seq):
    a = args(id=id, seq=seq, pt=pt, lc=lc, agent=agent, params=params, read_seq=read_seq)
    assert kq(CHAIN, **a) == [ref_digest(tagged(a))]
