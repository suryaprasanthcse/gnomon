"""Kernel.sha256 (master §8.2): FIPS 180-4 vectors and equivalence with hashlib over manual UTF-8."""

import hashlib

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1a]

FIPS_VECTORS = [
    ("", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    (
        "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
    ),
]

# NUL cannot travel in a Cypher parameter header; every other code point outside the surrogate block can.
TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"), max_size=2000)


@pytest.mark.parametrize(("text", "expected"), FIPS_VECTORS, ids=["empty", "abc", "two-block"])
def test_fips_vectors(kq, text, expected):
    assert kq("RETURN Kernel.sha256($s)", s=text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "é",                      # 2-byte UTF-8
        "€ ह ጀ",                  # 3-byte UTF-8
        "😀𝄞𐍈",                   # astral plane: surrogate pairs in UTF-16, 4 bytes in UTF-8
        "a" * 55,                 # padding fits one block exactly
        "a" * 56,                 # padding spills into a second block
        "a" * 64,
        "x" * 1000 + "😀" * 300,  # several blocks, mixed widths
    ],
    ids=["2-byte", "3-byte", "astral", "55", "56", "64", "long-mixed"],
)
def test_utf8_and_block_boundaries(kq, text):
    assert kq("RETURN Kernel.sha256($s)", s=text) == [hashlib.sha256(text.encode("utf-8")).hexdigest()]


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(text=TEXT)
def test_matches_hashlib(kq, text):
    assert kq("RETURN Kernel.sha256($s)", s=text) == [hashlib.sha256(text.encode("utf-8")).hexdigest()]


@pytest.mark.parametrize("arg", ["1", "null", "[1]", "true"])
def test_rejects_non_string(kq, arg):
    with pytest.raises(Exception, match="SHA256_NOT_STRING"):
        kq(f"RETURN Kernel.sha256({arg})")
