"""Shape normalization (master §16.2): literals lifted to parameters, the query printed canonically, SHA-256 of it.

Printing puts one space between tokens except next to ( ) [ ] { } . : , so the text stays valid Cypher. Numbers that
bound a variable-length pattern (`*0..4`) are structure, not data, and stay in the text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from gnomon.footprint.parser import tokenize

KEYWORDS = frozenset("""MATCH OPTIONAL WHERE WITH RETURN ORDER BY SKIP LIMIT UNWIND AS AND OR XOR NOT IN IS NULL DISTINCT
ASC DESC CASE WHEN THEN ELSE END UNION ALL STARTS ENDS CONTAINS EXISTS CALL YIELD""".split())
TIGHT = frozenset(["(", ")", "[", "]", "{", "}", ".", ":", ",", "*", ".."])


@dataclass(frozen=True)
class Shape:
    text: str
    sha: str
    params: dict


def _number(text: str):
    return float(text) if any(c in text for c in ".eE") else int(text)


def normalize(cypher: str, params: Mapping | None = None) -> Shape:
    toks = [t for t in tokenize(cypher) if t.kind != "eof"]
    lifted = dict(params or {})
    out: list[str] = []
    n = 0
    for i, tok in enumerate(toks):
        prev = toks[i - 1].text if i else ""
        nxt = toks[i + 1].text if i + 1 < len(toks) else ""
        if tok.kind == "str" or (tok.kind == "num" and prev not in ("*", "..") and nxt != ".."):
            name = f"_l{n}"
            n += 1
            lifted[name] = tok.text if tok.kind == "str" else _number(tok.text)
            out.append(f"${name}")
        elif tok.kind == "param":
            out.append(f"${tok.text}")
        elif tok.kind == "id":
            out.append(tok.text.upper() if tok.upper in KEYWORDS else tok.text)
        else:
            out.append(tok.text)
    text = ""
    for i, piece in enumerate(out):
        before = out[i - 1] if i else ""
        if i and ((piece not in TIGHT and before not in TIGHT)
                  or (before in (")", "]", "}") and piece not in TIGHT)
                  or (before in KEYWORDS and piece in ("(", "[", "{"))):
            text += " "
        text += piece
    return Shape(text, hashlib.sha256(text.encode("utf-8")).hexdigest(), lifted)
