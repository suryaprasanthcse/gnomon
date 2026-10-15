"""Rule definitions and SHA pinning (master §5.1, §5.2).

A rule's identity is `sha = SHA-256(JCS(def_json))` over the fields of §5.1, with its body already
macro-expanded and family placeholders substituted. Loading reads YAML: either a single rule file
(`load_rule_file`) or the registry (`load_registry`, gnomon/rules/registry.yaml). A body comes from
`body_file` (relative to the YAML file) or an inline `body`.

The registry file also holds the §3.3 fact types and the family tables (§5.2): each family names a
template file (sections marked `//## <NAME>`), shared defaults and substitutions, guard-probe lists,
and one entry per member. `probe_macros` (such as <NOW>) are substituted into guard probes only.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from gnomon.kernel import macros
from gnomon.kernel.jcs import canonicalize

RULES_DIR = Path(__file__).resolve().parents[1] / "rules"
REGISTRY_FILE = RULES_DIR / "registry.yaml"

_LIST_FIELDS = ("caller", "reads", "writes", "key_templates", "view_templates")
_KNOWN_KEYS = {
    "name", "version", "class", "layer", "family", "caller", "prelude", "requires_decision", "gated", "evidence",
    "reads", "writes", "key_templates", "view_templates", "guard_probes", "body", "body_file", "lint_sha",
}


@dataclass(frozen=True)
class RuleDef:
    name: str
    version: int
    cls: str
    layer: str
    caller: tuple[str, ...]
    prelude: str
    body: str
    family: str | None = None
    requires_decision: bool = False
    gated: bool = False
    evidence: bool = False
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    key_templates: tuple[str, ...] = ()
    view_templates: tuple[str, ...] = ()
    guard_probes: tuple[Mapping[str, str], ...] = ()
    lint_sha: str | None = None

    def definition(self) -> dict:
        """The hashed fields of §5.1; `caller` is sorted, every other list keeps its order."""
        return {
            "name": self.name,
            "version": self.version,
            "class": self.cls,
            "layer": self.layer,
            "family": self.family,
            "caller": sorted(self.caller),
            "prelude": self.prelude,
            "requires_decision": self.requires_decision,
            "gated": self.gated,
            "evidence": self.evidence,
            "reads": list(self.reads),
            "writes": list(self.writes),
            "key_templates": list(self.key_templates),
            "view_templates": list(self.view_templates),
            "guard_probes": [{"name": g["name"], "cypher": g["cypher"]} for g in self.guard_probes],
            "body": self.body,
        }

    @property
    def def_json(self) -> str:
        return canonicalize(self.definition())

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.def_json.encode("utf-8")).hexdigest()

    def to_param(self) -> dict:
        """One `$p.rules[]` element for R0 and M3: the definition plus def_json, sha and stored extras."""
        definition = self.definition()
        return {
            **definition,
            "def_json": canonicalize(definition),
            "sha": self.sha,
            "guard_probes_json": canonicalize(definition["guard_probes"]),
            "lint_sha": self.lint_sha,
        }


class Registry:
    """Rules by name, in registration order, and the fact types (§3.3) genesis records."""

    def __init__(self, rules: Iterable[RuleDef], fact_types: Iterable[Mapping] = ()) -> None:
        self._rules: dict[str, RuleDef] = {}
        self._fact_types = [dict(ft) for ft in fact_types]
        for rule in rules:
            if rule.name in self._rules:
                raise ValueError(f"rule {rule.name!r} is registered twice")
            self._rules[rule.name] = rule

    def rules(self) -> list[RuleDef]:
        return list(self._rules.values())

    def fact_types(self) -> list[dict]:
        return [dict(ft) for ft in self._fact_types]

    def get(self, name: str) -> RuleDef:
        try:
            return self._rules[name]
        except KeyError:
            raise KeyError(f"unknown rule {name!r}") from None


def rule_from_mapping(spec: Mapping, base_dir: Path) -> RuleDef:
    unknown = set(spec) - _KNOWN_KEYS
    if unknown:
        raise ValueError(f"rule {spec.get('name')!r}: unknown field(s) {sorted(unknown)}")
    if ("body" in spec) == ("body_file" in spec):
        raise ValueError(f"rule {spec.get('name')!r}: give exactly one of body and body_file")
    body = spec["body"] if "body" in spec else (base_dir / spec["body_file"]).read_text(encoding="utf-8")
    requires_decision = bool(spec.get("requires_decision", False))
    lists = {f: tuple(spec.get(f) or ()) for f in _LIST_FIELDS}
    return RuleDef(
        name=spec["name"],
        version=int(spec["version"]),
        cls=spec["class"],
        layer=spec["layer"],
        prelude=spec["prelude"],
        body=macros.expand(body),
        family=spec.get("family"),
        requires_decision=requires_decision,
        gated=bool(spec.get("gated", False)),
        evidence=bool(spec.get("evidence", requires_decision)),
        guard_probes=tuple({"name": g["name"], "cypher": g["cypher"]} for g in spec.get("guard_probes") or ()),
        lint_sha=spec.get("lint_sha"),
        **lists,
    )


def load_rule_file(path: Path) -> RuleDef:
    path = Path(path)
    return rule_from_mapping(yaml.safe_load(path.read_text(encoding="utf-8")), path.parent)


def _substitute(text: str, substitutions: Mapping[str, str]) -> str:
    for placeholder, value in substitutions.items():
        text = text.replace(placeholder, value)
    return text


def _sections(text: str) -> dict[str, str]:
    """Template sections, each starting at a line `//## <NAME>`; a file without markers is one section ""."""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines(keepends=True):
        if line.startswith("//## "):
            current = line[5:].strip()
            sections[current] = []
        else:
            sections.setdefault(current, []).append(line)
    return {name: "".join(lines) for name, lines in sections.items()}


def family_members(family: Mapping, base_dir: Path) -> list[RuleDef]:
    """Expand one family table into its member rules (§5.2)."""
    sections = _sections((base_dir / family["template"]).read_text(encoding="utf-8"))
    out = []
    for entry in family["members"]:
        member = entry["member"]
        spec = {**family["defaults"], "name": family["family"], "family": family["family"],
                "body": sections[entry.get("section", "")],
                "guard_probes": family["guard_probes"][entry["probes"]]}
        spec.update({k: v for k, v in entry.items() if k in _KNOWN_KEYS})
        template = rule_from_mapping(spec, base_dir)
        substitutions = {family["member_placeholder"]: member, **family.get("substitutions", {}),
                         **entry.get("substitutions", {})}
        out.append(expand_family(template, member, substitutions))
    return out


def _with_probe_macros(rule: RuleDef, probe_macros: Mapping[str, str]) -> RuleDef:
    if not probe_macros:
        return rule
    probes = tuple({"name": g["name"], "cypher": _substitute(g["cypher"], probe_macros)} for g in rule.guard_probes)
    return dataclasses.replace(rule, guard_probes=probes)


def load_registry(path: Path = REGISTRY_FILE) -> Registry:
    path = Path(path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules = [rule_from_mapping(spec, path.parent) for spec in document.get("rules", [])]
    for family in document.get("families", []):
        rules.extend(family_members(family, path.parent))
    probe_macros = document.get("probe_macros") or {}
    return Registry((_with_probe_macros(r, probe_macros) for r in rules), document.get("fact_types", []))


def register_rule_params(rule: RuleDef) -> dict:
    """`$p` for M3 RegisterRule (§9.13): the definition, its def_json and its sha."""
    return {"def": rule.to_param(), "def_json": rule.def_json, "sha": rule.sha}


def expand_family(template: RuleDef, member: str, substitutions: Mapping[str, str]) -> RuleDef:
    """A family member (§5.2): placeholders such as <REL>, <S>, <O>, <PROJ> substituted, then macros expanded."""

    def sub(text: str) -> str:
        return _substitute(text, substitutions)

    return dataclasses.replace(
        template,
        name=f"{template.family or template.name}.{member}",
        family=template.family or template.name,
        body=macros.expand(sub(template.body)),
        reads=tuple(map(sub, template.reads)),
        writes=tuple(map(sub, template.writes)),
        key_templates=tuple(map(sub, template.key_templates)),
        view_templates=tuple(map(sub, template.view_templates)),
        guard_probes=tuple({"name": g["name"], "cypher": sub(g["cypher"])} for g in template.guard_probes),
    )
