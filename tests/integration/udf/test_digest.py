"""Kernel.digest (master §8.2): SHA-256 over the netstring encoding of str | int | null fields."""

import hashlib

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1a]

SAFE = 2**53 - 1
TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"), max_size=200)
FIELD = st.one_of(st.none(), st.integers(min_value=-SAFE, max_value=SAFE), TEXT)


def test_netstring_example(kq, ref_enc):
    assert ref_enc(["abc", 7, None]) == "3:abc,1:7,~,"
    expected = hashlib.sha256(b"3:abc,1:7,~,").hexdigest()
    assert kq("RETURN Kernel.digest(['abc', 7, null])") == [expected]


def test_empty_list_is_sha_of_empty_string(kq):
    assert kq("RETURN Kernel.digest([])") == [hashlib.sha256(b"").hexdigest()]


def test_byte_length_not_code_units(kq):
    # "😀" is 2 UTF-16 code units and 4 UTF-8 bytes; the prefix must count bytes.
    assert kq("RETURN Kernel.digest(['😀', 'é'])") == [hashlib.sha256("4:😀,2:é,".encode()).hexdigest()]


@pytest.mark.parametrize(
    ("value", "text"),
    [(0, "0"), (-5, "-5"), (SAFE, "9007199254740991"), (-SAFE, "-9007199254740991"), (1791000000000, "1791000000000")],
)
def test_integer_rendering(kq, value, text):
    expected = hashlib.sha256(f"{len(text)}:{text},".encode()).hexdigest()
    assert kq("RETURN Kernel.digest([$v])", v=value) == [expected]


def test_prefix_free_distinguishes_splits(kq):
    joined = kq("RETURN Kernel.digest(['ab', 'c'])")[0]
    split = kq("RETURN Kernel.digest(['a', 'bc'])")[0]
    as_int = kq("RETURN Kernel.digest(['7'])")[0]
    as_str = kq("RETURN Kernel.digest([7])")[0]
    empty = kq("RETURN Kernel.digest([''])")[0]
    null = kq("RETURN Kernel.digest([null])")[0]
    assert joined != split
    assert as_int == as_str  # the encoding is of the decimal text (§8.2); kinds are fixed per position
    assert empty != null


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(fields=st.lists(FIELD, max_size=12))
def test_matches_reference(kq, ref_digest, fields):
    assert kq("RETURN Kernel.digest($f)", f=fields) == [ref_digest(fields)]


@pytest.mark.parametrize(
    "fields",
    ["[1.5]", "['a', 2.25]", "[true]", "[false, 'x']", "[[1]]", "['a', ['b']]", "[{a: 1}]", "[9007199254740993]"],
    ids=["float", "float-late", "true", "false", "nested", "nested-late", "map", "unsafe-int"],
)
def test_rejects_float_bool_nested(kq, fields):
    with pytest.raises(Exception, match="DIGEST_BAD_FIELD"):
        kq(f"RETURN Kernel.digest({fields})")


@pytest.mark.parametrize("arg", ["'abc'", "7", "null", "{a: 1}"])
def test_rejects_non_list(kq, arg):
    with pytest.raises(Exception, match="DIGEST_NOT_LIST"):
        kq(f"RETURN Kernel.digest({arg})")
