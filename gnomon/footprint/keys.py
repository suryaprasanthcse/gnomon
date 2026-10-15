"""Key templates (master §5.4, §7.3, Appendix B).

    key      := segment (":" segment)*
    segment  := literal | "*" | "{" placeholder "}"
    placeholder := "pid" | "p." path | "p." field "[]" | "p." field "[]." field | "m." field   (m: P2/P3 only)

Keys are a pure function of (rule, params): a template may name only $p fields, the proposal id, or, in
federation preludes, fields of the message $m (lint L5). A missing or null value yields no key; a list
placeholder yields one key per non-null element; keys keep template order, then list order, without
duplicates.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

_FIELD = r"[A-Za-z_]\w*"
_PATH = re.compile(rf"p\.({_FIELD}(?:\.{_FIELD})*)")
_LIST = re.compile(rf"p\.({_FIELD})\[\](?:\.({_FIELD}))?")
_MESSAGE = re.compile(rf"m\.({_FIELD})")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
MESSAGE_PRELUDES = ("P2", "P3")


class KeyTemplateError(ValueError):
    """A malformed key template (a rule bug)."""


class KeyValueError(KeyTemplateError):
    """Params whose shape a template cannot use, such as a scalar where a list placeholder expects a list."""


def root(template: str) -> str:
    return template.split(":", 1)[0]


def covers(template: str) -> str:
    """What a key protects: "state" (mutable properties of one instance), "all" (one instance, any change),
    "phantom" (any instance of the type) or "literal" (a fixed key such as Head)."""
    segments = template.split(":")
    if not any(_PLACEHOLDER.fullmatch(s) for s in segments):
        return "phantom" if "*" in segments else "literal"
    return "state" if segments[-1] == "state" else "all"


def template_problems(template: str, prelude: str = "P0") -> list[str]:
    """Every reason `template` is not a valid key template for a rule with this prelude (empty if valid)."""
    if not template:
        return ["key template is empty"]
    stripped = _PLACEHOLDER.sub("", template)
    if "{" in stripped or "}" in stripped:
        return [f"key template {template!r}: unbalanced braces"]
    problems = []
    lists = 0
    for segment in template.split(":"):
        if not segment:
            problems.append(f"key template {template!r}: empty segment")
            continue
        found = _PLACEHOLDER.findall(segment)
        if not found:
            continue
        if len(found) > 1 or not _PLACEHOLDER.fullmatch(segment):
            problems.append(f"key template {template!r}: a segment is a literal, * or exactly one placeholder")
            continue
        body = found[0]
        if body == "pid" or _PATH.fullmatch(body):
            continue
        if _LIST.fullmatch(body):
            lists += 1
            continue
        if _MESSAGE.fullmatch(body):
            if prelude not in MESSAGE_PRELUDES:
                problems.append(f"key template {template!r}: {{{body}}} is allowed only in P2/P3 rules")
            continue
        if body.startswith(("p.", "m.")):
            problems.append(f"key template {template!r}: malformed placeholder {{{body}}}")
        else:
            problems.append(f"key template {template!r}: {{{body}}} — templates may use only $p fields, {{pid}} or "
                            "{m.<field>}")
    if lists > 1:
        problems.append(f"key template {template!r}: more than one list placeholder")
    return problems


def _render(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _alternatives(template: str, body: str, p: Mapping, pid: str, m: Mapping | None) -> list[str]:
    if body == "pid":
        return [pid]
    if path := _PATH.fullmatch(body):
        value: object = p
        for part in path.group(1).split("."):
            value = value.get(part) if isinstance(value, Mapping) else None
        return [] if value is None else [_render(value)]
    if listed := _LIST.fullmatch(body):
        items = p.get(listed.group(1))
        if items is None:
            return []
        if not isinstance(items, (list, tuple)):
            raise KeyValueError(f"key template {template!r}: p.{listed.group(1)} is not a list")
        attr = listed.group(2)
        if attr is None:
            return [_render(v) for v in items if v is not None]
        return [_render(v[attr]) for v in items if isinstance(v, Mapping) and v.get(attr) is not None]
    message = _MESSAGE.fullmatch(body)
    value = (m or {}).get(message.group(1)) if message else None
    return [] if value is None else [_render(value)]


def instantiate(templates: Sequence[str], p: Mapping, pid: str, m: Mapping | None = None) -> list[str]:
    keys: list[str] = []
    for template in templates:
        problems = template_problems(template, prelude="P2")
        if problems:
            raise KeyTemplateError("bad key template: " + "; ".join(problems))
        expansions = [""]
        for i, segment in enumerate(template.split(":")):
            prefix = ":" if i else ""
            if placeholder := _PLACEHOLDER.fullmatch(segment):
                values = _alternatives(template, placeholder.group(1), p, pid, m)
                expansions = [e + prefix + v for e in expansions for v in values]
            else:
                expansions = [e + prefix + segment for e in expansions]
        for key in expansions:
            if key not in keys:
                keys.append(key)
    return keys
