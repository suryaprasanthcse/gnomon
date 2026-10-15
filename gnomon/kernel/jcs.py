"""JSON Canonicalization Scheme, RFC 8785 (master §0.3).

Object keys are sorted by their UTF-16 code units, strings use the minimal JSON escapes, and numbers
are serialized as ECMAScript's Number.prototype.toString does. Integers must be exactly representable
as IEEE doubles (|n| <= 2^53 - 1); NaN and infinities are refused.
"""

from __future__ import annotations

import math
from decimal import Decimal

MAX_SAFE_INTEGER = 2**53 - 1
_SHORT_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


class JcsError(ValueError):
    """The value has no exact RFC 8785 serialization."""


def canonicalize(value: object) -> str:
    out: list[str] = []
    _write(value, out)
    return "".join(out)


def _write(value: object, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise JcsError(f"integer {value} is outside ±(2^53 - 1)")
        out.append(str(value))
    elif isinstance(value, float):
        out.append(_number(value))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _write(item, out)
        out.append("]")
    elif isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise JcsError("object keys must be strings")
        out.append("{")
        for i, key in enumerate(sorted(value, key=_utf16_key)):
            if i:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _write(value[key], out)
        out.append("}")
    else:
        raise JcsError(f"cannot canonicalize {type(value).__name__}")


def _utf16_key(key: str) -> bytes:
    return key.encode("utf-16-be", "surrogatepass")


def _string(s: str) -> str:
    parts = ['"']
    for ch in s:
        if ch in _SHORT_ESCAPES:
            parts.append(_SHORT_ESCAPES[ch])
        elif ch < " ":
            parts.append(f"\\u{ord(ch):04x}")
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def _number(x: float) -> str:
    """ECMAScript Number::toString over the shortest round-trip digits (Python repr)."""
    if not math.isfinite(x):
        raise JcsError(f"{x!r} has no JSON representation")
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    _, digit_tuple, exponent = Decimal(repr(abs(x))).as_tuple()
    digits = "".join(map(str, digit_tuple)).lstrip("0")
    stripped = digits.rstrip("0")
    exponent += len(digits) - len(stripped)
    digits = stripped
    k = len(digits)
    n = k + exponent  # value = 0.<digits> * 10^n
    if k <= n <= 21:
        text = digits + "0" * (n - k)
    elif 0 < n <= 21:
        text = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        text = "0." + "0" * -n + digits
    else:
        e = n - 1
        mantissa = digits[0] + ("." + digits[1:] if k > 1 else "")
        text = f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + text
