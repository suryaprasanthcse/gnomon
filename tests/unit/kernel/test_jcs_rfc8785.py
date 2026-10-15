"""JCS canonical JSON (RFC 8785, master §0.3): key order, ES6 numbers, string escaping."""

import json
import math

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g1b]


def canon(value):
    from gnomon.kernel.jcs import canonicalize

    return canonicalize(value)


def test_rfc8785_section_3_2_2_example():
    raw = (
        '{"numbers":[333333333.33333329,1E30,4.50,2e-3,0.000000000000000000000000001],'
        '"string":"\\u20ac$\\u000F\\u000aA\'\\u0042\\u0022\\u005c\\\\\\"\\/",'
        '"literals":[null,true,false]}'
    )
    expected = (
        '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )
    assert canon(json.loads(raw)) == expected


def test_keys_sorted_by_utf16_code_units():
    # "\U0001F600" is a surrogate pair (0xD83D ...) and sorts before "ﬁ" in UTF-16,
    # although its code point is larger.
    value = {"ﬁ": 1, "\U0001f600": 2, "b": 3, "a": 4, "aa": 5, "A": 6, "": 7}
    assert canon(value) == '{"":7,"A":6,"a":4,"aa":5,"b":3,"\U0001f600":2,"ﬁ":1}'


def test_nested_objects_and_arrays_keep_array_order():
    value = {"z": [3, {"b": 1, "a": [2, 1]}], "a": {"y": None, "x": True}}
    assert canon(value) == '{"a":{"x":true,"y":null},"z":[3,{"a":[2,1],"b":1}]}'


@pytest.mark.parametrize(
    ("number", "text"),
    [
        (0, "0"), (-0.0, "0"), (1, "1"), (-1, "-1"), (4.5, "4.5"), (2e-3, "0.002"), (1e-6, "0.000001"),
        (1e-7, "1e-7"), (1e21, "1e+21"), (1e20, "100000000000000000000"), (123456789012345680000.0, "123456789012345680000"),
        (5e-324, "5e-324"), (1.7976931348623157e308, "1.7976931348623157e+308"), (0.1, "0.1"),
        (-1.5e-9, "-1.5e-9"), (9007199254740991, "9007199254740991"), (1.0, "1"), (100.0, "100"),
        (333333333.33333329, "333333333.3333333"), (1.2345e25, "1.2345e+25"),
    ],
)
def test_es6_number_serialization(number, text):
    assert canon(number) == text


@pytest.mark.parametrize(
    ("string", "text"),
    [
        ("plain", '"plain"'),
        ("quote\"back\\slash", '"quote\\"back\\\\slash"'),
        ("\b\f\n\r\t", '"\\b\\f\\n\\r\\t"'),
        ("\x00\x01\x1f", '"\\u0000\\u0001\\u001f"'),
        ("\x7f/é€😀", '"\x7f/é€😀"'),
        ("  ", '"  "'),
    ],
    ids=["plain", "quote-backslash", "short-escapes", "control-hex-lowercase", "raw-non-ascii", "line-separators"],
)
def test_string_escaping(string, text):
    assert canon(string) == text


def test_literals_and_empty_containers():
    assert canon([None, True, False, {}, [], ""]) == '[null,true,false,{},[],""]'


def test_tuples_are_arrays():
    assert canon({"k": ("a", 1)}) == '{"k":["a",1]}'


@pytest.mark.parametrize(
    "bad",
    [math.nan, math.inf, -math.inf, 2**53, -(2**53), {1: "x"}, {"s": {1, 2}}, b"bytes", object()],
    ids=["nan", "inf", "-inf", "int-2^53", "int--2^53", "non-string-key", "set", "bytes", "object"],
)
def test_rejects_values_json_cannot_carry_exactly(bad):
    from gnomon.kernel.jcs import JcsError

    with pytest.raises(JcsError):
        canon(bad)


def test_output_is_utf8_stable_and_idempotent():
    value = {"b": [1, 2.5, "é"], "a": {"c": None}}
    once = canon(value)
    assert canon(json.loads(once)) == once
