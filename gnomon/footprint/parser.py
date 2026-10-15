"""Static extraction for GNOMON's constrained Cypher dialect (master §7.4; DECISIONS D-16).

A hand-written tokenizer and recursive-descent parser (the accepted grammar is in grammar/gnomon_cypher.ebnf)
turn a rule body into clauses and expression trees; `extract` then walks them once and reports the body's
footprint in a type-level vocabulary:

    Label / TYPE            nodes with that label, relationships of that type (matched, created, deleted)
    Label.prop / TYPE.prop  a property of those nodes or relationships

Variables bound by the kernel context (KCTX: h, causes, nseq, now, now_lc) and values derived from
$parameters are not graph elements. A variable aliased through WITH keeps its type when the alias is the
variable itself, head/last(collect(v)), or collect(v) (a list of v's type); a FOREACH variable takes the
type of the single graph variable its list expression mentions.

`guard_reads` are the reads made before the first write clause (MATCH and OPTIONAL MATCH patterns, WHERE and
WITH): everything that decides whether, and on which rows, the writes happen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KCTX_VARS = frozenset({"h", "causes", "nseq", "now", "now_lc"})
WRITE_CLAUSES = frozenset({"CREATE", "MERGE", "SET", "DELETE", "REMOVE", "FOREACH"})
READ_CLAUSES = frozenset({"MATCH", "OPTIONAL MATCH", "UNWIND", "WHERE"})
AGGREGATES = frozenset({"count", "collect", "sum", "max", "min", "avg", "stdev", "stdevp", "percentilecont",
                        "percentiledisc"})
QUANTIFIERS = frozenset({"any", "all", "none", "single"})
_CLAUSE_START = {"MATCH", "OPTIONAL", "WHERE", "WITH", "RETURN", "UNWIND", "CREATE", "MERGE", "SET", "DELETE",
                 "DETACH", "REMOVE", "FOREACH"}


class ParseError(ValueError):
    """The body is not in the accepted dialect."""


# ─────────────────────────────────────────────────────────────── tokenizer

@dataclass(frozen=True)
class Tok:
    kind: str  # id, kw-able id, str, num, param, op, eof
    text: str
    pos: int

    @property
    def upper(self) -> str:
        return self.text.upper()


_OPS = ["<=", ">=", "<>", "=~", "->", "<-", "..", "+=", "(", ")", "[", "]", "{", "}", ",", ":", ".", "|", ";", "=",
        "<", ">", "+", "-", "*", "/", "%", "^", "!"]
_NUMBER = re.compile(r"\d+(\.\d+)?([eE][+-]?\d+)?")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def tokenize(text: str) -> list[Tok]:
    toks: list[Tok] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise ParseError(f"unterminated comment at {i}")
            i = j + 2
        elif c in "'\"":
            j, out = i + 1, []
            while j < n and text[j] != c:
                if text[j] == "\\" and j + 1 < n:
                    out.append(text[j + 1])
                    j += 2
                else:
                    out.append(text[j])
                    j += 1
            if j >= n:
                raise ParseError(f"unterminated string at {i}")
            toks.append(Tok("str", "".join(out), i))
            i = j + 1
        elif c == "`":
            j = text.find("`", i + 1)
            if j < 0:
                raise ParseError(f"unterminated identifier at {i}")
            toks.append(Tok("id", text[i + 1:j], i))
            i = j + 1
        elif c == "$":
            m = _IDENT.match(text, i + 1)
            if not m:
                raise ParseError(f"bad parameter at {i}")
            toks.append(Tok("param", m.group(0), i))
            i = m.end()
        elif c.isdigit():
            m = _NUMBER.match(text, i)
            toks.append(Tok("num", m.group(0), i))
            i = m.end()
        elif m := _IDENT.match(text, i):
            toks.append(Tok("id", m.group(0), i))
            i = m.end()
        else:
            for op in _OPS:
                if text.startswith(op, i):
                    toks.append(Tok("op", op, i))
                    i += len(op)
                    break
            else:
                raise ParseError(f"unexpected character {c!r} at {i}")
    toks.append(Tok("eof", "", n))
    return toks


# ─────────────────────────────────────────────────────────────── syntax tree

@dataclass
class E:
    """An expression node. kind: var, param, lit, prop, call, list, map, case, comp, label, op, index, star."""
    kind: str
    name: str = ""
    kids: list = field(default_factory=list)
    distinct: bool = False
    local: str = ""  # comprehension / quantifier / reduce variable


@dataclass
class NodePat:
    var: str
    labels: list[str]
    props: list[tuple[str, E]]
    param_props: E | None = None


@dataclass
class RelPat:
    var: str
    types: list[str]
    props: list[tuple[str, E]]
    var_length: bool


@dataclass
class Part:
    nodes: list[NodePat]
    rels: list[RelPat]  # rels[i] joins nodes[i] and nodes[i + 1]


@dataclass
class Clause:
    kind: str
    parts: list[Part] = field(default_factory=list)
    exprs: list[E] = field(default_factory=list)
    items: list[tuple[E, str]] = field(default_factory=list)  # WITH/RETURN/UNWIND items: (expr, alias)
    sets: list[tuple[str, E, E]] = field(default_factory=list)  # SET: (op, target, value)
    var: str = ""
    body: list[Clause] = field(default_factory=list)  # FOREACH


class _Parser:
    def __init__(self, text: str) -> None:
        self.toks = tokenize(text)
        self.i = 0

    # ── token helpers
    @property
    def tok(self) -> Tok:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def at(self, *texts: str) -> bool:
        t = self.tok
        return (t.kind == "op" and t.text in texts) or (t.kind == "id" and t.upper in texts)

    def take(self, *texts: str) -> Tok:
        if not self.at(*texts):
            raise ParseError(f"expected {' or '.join(texts)} at {self.tok.pos}, found {self.tok.text!r}")
        t = self.tok
        self.i += 1
        return t

    def accept(self, *texts: str) -> bool:
        if self.at(*texts):
            self.i += 1
            return True
        return False

    def ident(self) -> str:
        if self.tok.kind != "id":
            raise ParseError(f"expected a name at {self.tok.pos}, found {self.tok.text!r}")
        t = self.tok
        self.i += 1
        return t.text

    # ── clauses
    def body(self) -> list[Clause]:
        clauses = self.clauses(top=True)
        if self.tok.kind != "eof":
            raise ParseError(f"unexpected {self.tok.text!r} at {self.tok.pos}")
        return clauses

    def clauses(self, top: bool) -> list[Clause]:
        out: list[Clause] = []
        while self.tok.kind == "id" and self.tok.upper in _CLAUSE_START:
            out.extend(self.clause())
        if top and self.tok.kind != "eof":
            raise ParseError(f"unknown clause {self.tok.text!r} at {self.tok.pos}")
        return out

    def clause(self) -> list[Clause]:
        word = self.tok.upper
        if word == "OPTIONAL":
            self.i += 1
            self.take("MATCH")
            return self.match("OPTIONAL MATCH")
        if word == "MATCH":
            self.i += 1
            return self.match("MATCH")
        if word == "WHERE":
            self.i += 1
            return [Clause("WHERE", exprs=[self.expr()])]
        if word in ("WITH", "RETURN"):
            self.i += 1
            return self.projection(word)
        if word == "UNWIND":
            self.i += 1
            e = self.expr()
            self.take("AS")
            return [Clause("UNWIND", items=[(e, self.ident())])]
        if word == "CREATE":
            self.i += 1
            return [Clause("CREATE", parts=self.pattern())]
        if word == "MERGE":
            self.i += 1
            c = Clause("MERGE", parts=[self.part()])
            while self.accept("ON"):
                self.take("CREATE", "MATCH")
                self.take("SET")
                c.sets.extend(self.set_items())
            return [c]
        if word == "SET":
            self.i += 1
            return [Clause("SET", sets=self.set_items())]
        if word in ("DELETE", "DETACH"):
            self.i += 1
            if word == "DETACH":
                self.take("DELETE")
            exprs = [self.expr()]
            while self.accept(","):
                exprs.append(self.expr())
            return [Clause("DELETE", exprs=exprs)]
        if word == "REMOVE":
            self.i += 1
            exprs = [self.postfix()]
            while self.accept(","):
                exprs.append(self.postfix())
            return [Clause("REMOVE", exprs=exprs)]
        if word == "FOREACH":
            self.i += 1
            self.take("(")
            var = self.ident()
            self.take("IN")
            src = self.expr()
            self.take("|")
            inner = self.clauses(top=False)
            if not inner:
                raise ParseError(f"empty FOREACH at {self.tok.pos}")
            self.take(")")
            return [Clause("FOREACH", var=var, exprs=[src], body=inner)]
        raise ParseError(f"unknown clause {self.tok.text!r} at {self.tok.pos}")

    def match(self, kind: str) -> list[Clause]:
        out = [Clause(kind, parts=self.pattern())]
        if self.at("WHERE"):
            self.i += 1
            out.append(Clause("WHERE", exprs=[self.expr()]))
        return out

    def projection(self, kind: str) -> list[Clause]:
        c = Clause(kind)
        self.accept("DISTINCT")
        while True:
            if self.accept("*"):
                c.items.append((E("star"), "*"))
            else:
                e = self.expr()
                alias = self.ident() if self.accept("AS") else (e.name if e.kind == "var" else "")
                if not alias:
                    raise ParseError(f"{kind} item needs an alias at {self.tok.pos}")
                c.items.append((e, alias))
            if not self.accept(","):
                break
        if self.accept("ORDER"):
            self.take("BY")
            while True:
                c.exprs.append(self.expr())
                self.accept("ASC", "DESC", "ASCENDING", "DESCENDING")
                if not self.accept(","):
                    break
        for word in ("SKIP", "LIMIT"):
            if self.accept(word):
                c.exprs.append(self.expr())
        out = [c]
        if kind == "WITH" and self.at("WHERE"):
            self.i += 1
            out.append(Clause("WHERE", exprs=[self.expr()]))
        return out

    def set_items(self) -> list[tuple[str, E, E]]:
        items = []
        while True:
            target = self.postfix()
            if target.kind == "label":
                items.append(("label", target, E("lit")))
            else:
                op = self.take("=", "+=").text
                items.append((op, target, self.expr()))
            if not self.accept(","):
                return items

    # ── patterns
    def pattern(self) -> list[Part]:
        parts = [self.part()]
        while self.accept(","):
            parts.append(self.part())
        return parts

    def part(self) -> Part:
        if self.tok.kind == "id" and self.peek().text == "=" and self.peek().kind == "op":
            self.i += 2  # named path p = (...)
        part = Part([self.node()], [])
        while self.at("-", "<-"):
            part.rels.append(self.rel())
            part.nodes.append(self.node())
        return part

    def node(self) -> NodePat:
        self.take("(")
        var = self.ident() if self.tok.kind == "id" else ""
        labels = []
        while self.accept(":"):
            labels.append(self.ident())
        props, param_props = [], None
        if self.at("{"):
            props = self.map_items()
        elif self.tok.kind == "param":
            param_props = E("param", self.tok.text)
            self.i += 1
        self.take(")")
        return NodePat(var, labels, props, param_props)

    def rel(self) -> RelPat:
        self.take("-", "<-")
        var, types, props, var_length = "", [], [], False
        if self.accept("["):
            var = self.ident() if self.tok.kind == "id" else ""
            if self.accept(":"):
                types.append(self.ident())
                while self.accept("|"):
                    self.accept(":")
                    types.append(self.ident())
            if self.accept("*"):
                var_length = True
                if self.tok.kind == "num":
                    self.i += 1
                if self.accept(".."):
                    if self.tok.kind == "num":
                        self.i += 1
            if self.at("{"):
                props = self.map_items()
            self.take("]")
        self.take("->", "-")
        return RelPat(var, types, props, var_length)

    def map_items(self) -> list[tuple[str, E]]:
        self.take("{")
        items = []
        if not self.at("}"):
            while True:
                key = self.ident() if self.tok.kind == "id" else self.take_str()
                self.take(":")
                items.append((key, self.expr()))
                if not self.accept(","):
                    break
        self.take("}")
        return items

    def take_str(self) -> str:
        if self.tok.kind != "str":
            raise ParseError(f"expected a map key at {self.tok.pos}")
        t = self.tok
        self.i += 1
        return t.text

    # ── expressions (precedence climbing)
    def expr(self) -> E:
        return self.binary(0)

    _LEVELS = [("OR",), ("XOR",), ("AND",)]

    def binary(self, level: int) -> E:
        if level < len(self._LEVELS):
            left = self.binary(level + 1)
            while self.at(*self._LEVELS[level]):
                op = self.tok.upper
                self.i += 1
                left = E("op", op, [left, self.binary(level + 1)])
            return left
        return self.negation()

    def negation(self) -> E:
        if self.accept("NOT"):
            return E("op", "NOT", [self.negation()])
        return self.comparison()

    def comparison(self) -> E:
        left = self.additive()
        while True:
            if self.at("=", "<>", "<", ">", "<=", ">=", "=~"):
                op = self.tok.text
                self.i += 1
                left = E("op", op, [left, self.additive()])
            elif self.at("IN"):
                self.i += 1
                left = E("op", "IN", [left, self.additive()])
            elif self.at("STARTS", "ENDS"):
                op = self.tok.upper
                self.i += 1
                self.take("WITH")
                left = E("op", op, [left, self.additive()])
            elif self.at("CONTAINS"):
                self.i += 1
                left = E("op", "CONTAINS", [left, self.additive()])
            elif self.at("IS"):
                self.i += 1
                negated = self.accept("NOT")
                self.take("NULL")
                left = E("op", "IS NOT NULL" if negated else "IS NULL", [left])
            else:
                return left

    def additive(self) -> E:
        left = self.multiplicative()
        while self.at("+", "-"):
            op = self.tok.text
            self.i += 1
            left = E("op", op, [left, self.multiplicative()])
        return left

    def multiplicative(self) -> E:
        left = self.power()
        while self.at("*", "/", "%"):
            op = self.tok.text
            self.i += 1
            left = E("op", op, [left, self.power()])
        return left

    def power(self) -> E:
        left = self.unary()
        while self.accept("^"):
            left = E("op", "^", [left, self.unary()])
        return left

    def unary(self) -> E:
        if self.at("-", "+"):
            op = self.tok.text
            self.i += 1
            return E("op", "u" + op, [self.unary()])
        return self.postfix()

    def postfix(self) -> E:
        e = self.atom()
        while True:
            if self.at(".") and self.peek().kind == "id":
                self.i += 1
                e = E("prop", self.ident(), [e])
            elif self.at("["):
                self.i += 1
                kids = [e]
                if not self.at(".."):
                    kids.append(self.expr())
                if self.accept(".."):
                    if not self.at("]"):
                        kids.append(self.expr())
                    self.take("]")
                    e = E("index", "slice", kids)
                else:
                    self.take("]")
                    e = E("index", "", kids)
            elif self.at(":") and e.kind == "var" and self.peek().kind == "id":
                labels = []
                while self.accept(":"):
                    labels.append(self.ident())
                e = E("label", "", [e])
                e.name = ",".join(labels)
            else:
                return e

    def atom(self) -> E:
        t = self.tok
        if t.kind == "num":
            self.i += 1
            return E("lit", t.text)
        if t.kind == "str":
            self.i += 1
            return E("lit", repr(t.text))
        if t.kind == "param":
            self.i += 1
            return E("param", t.text)
        if self.at("("):
            self.i += 1
            e = self.expr()
            self.take(")")
            return e
        if self.at("["):
            return self.list_or_comprehension()
        if self.at("{"):
            return E("map", "", [v for _, v in self.map_items()])
        if t.kind != "id":
            raise ParseError(f"unexpected {t.text!r} at {t.pos}")
        word = t.upper
        if word in ("TRUE", "FALSE", "NULL"):
            self.i += 1
            return E("lit", word)
        if word == "CASE":
            return self.case()
        # function call: name( or ns.name(
        if self.peek().text == "(" and self.peek().kind == "op":
            return self.call(self.ident())
        if self.peek().text == "." and self.peek(2).kind == "id" and self.peek(3).text == "(" and \
                self.peek(3).kind == "op":
            ns = self.ident()
            self.take(".")
            return self.call(f"{ns}.{self.ident()}")
        self.i += 1
        return E("var", t.text)

    def call(self, name: str) -> E:
        self.take("(")
        lname = name.lower()
        if lname in QUANTIFIERS and self.tok.kind == "id" and self.peek().upper == "IN":
            local = self.ident()
            self.take("IN")
            src = self.expr()
            self.take("WHERE")
            pred = self.expr()
            self.take(")")
            return E("comp", lname, [src, pred], local=local)
        if lname == "reduce":
            acc = self.ident()
            self.take("=")
            init = self.expr()
            self.take(",")
            local = self.ident()
            self.take("IN")
            src = self.expr()
            self.take("|")
            step = self.expr()
            self.take(")")
            node = E("comp", "reduce", [src, init, step], local=local)
            node.name = f"reduce:{acc}"
            return node
        distinct = self.accept("DISTINCT")
        args = []
        if self.accept("*"):
            args.append(E("star"))
        elif not self.at(")"):
            args.append(self.expr())
            while self.accept(","):
                args.append(self.expr())
        self.take(")")
        return E("call", name, args, distinct=distinct)

    def list_or_comprehension(self) -> E:
        self.take("[")
        if self.tok.kind == "id" and self.peek().upper == "IN" and self.peek().kind == "id":
            local = self.ident()
            self.take("IN")
            src = self.expr()
            pred = self.expr() if self.accept("WHERE") else E("lit", "TRUE")
            proj = self.expr() if self.accept("|") else E("var", local)
            self.take("]")
            return E("comp", "list", [src, pred, proj], local=local)
        items = []
        if not self.at("]"):
            items.append(self.expr())
            while self.accept(","):
                items.append(self.expr())
        self.take("]")
        return E("list", "", items)

    def case(self) -> E:
        self.take("CASE")
        kids = []
        if not self.at("WHEN"):
            kids.append(self.expr())
        while self.accept("WHEN"):
            kids.append(self.expr())
            self.take("THEN")
            kids.append(self.expr())
        if self.accept("ELSE"):
            kids.append(self.expr())
        self.take("END")
        return E("case", "", kids)


def parse(text: str) -> list[Clause]:
    return _Parser(text).body()


# ─────────────────────────────────────────────────────────────── analysis

@dataclass(frozen=True)
class VarType:
    kind: str  # node, rel, list, value, kernel
    labels: frozenset = frozenset()
    part: int = -1  # pattern part that bound it (for count guards)
    anchored: bool = False  # bound by a unique property (L2)
    created: int = -1  # creation group id when created in this body


VALUE = VarType("value")


@dataclass
class MatchInfo:
    """One MATCH / OPTIONAL MATCH pattern part, for L3."""
    clause: str
    description: str
    anchored: bool
    single_row: bool


@dataclass
class Pick:
    """A positional pick from an unordered collection (head/last/index of collect(v)), for L2."""
    alias: str
    var: str
    count_alias: str | None
    anchored: bool


@dataclass
class Footprint:
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    guard_reads: set[str] = field(default_factory=set)
    created: set[frozenset] = field(default_factory=set)
    calls: set[str] = field(default_factory=set)
    clauses: tuple[str, ...] = ()
    set_conflicts: set[str] = field(default_factory=set)
    reads_after_write: list[str] = field(default_factory=list)
    matches: list[MatchInfo] = field(default_factory=list)
    picks: list[Pick] = field(default_factory=list)
    where_names: set[str] = field(default_factory=set)
    may_end_with_many_rows: bool = False


class _Analyzer:
    def __init__(self, indexed: set[tuple[str, str]], unique: set[tuple[str, frozenset]]) -> None:
        self.f = Footprint()
        self.indexed = indexed
        self.unique = unique
        self.wrote = False
        self.parts = 0
        self.groups: dict[int, set[str]] = {}
        self.next_group = 0
        self.multi = False

    # ── recording
    def read(self, element: str) -> None:
        self.f.reads.add(element)
        if not self.wrote:
            self.f.guard_reads.add(element)

    def write(self, element: str) -> None:
        self.f.writes.add(element)

    # ── expressions
    def type_of(self, e: E, env: dict[str, VarType]) -> VarType:
        if e.kind == "var":
            return env.get(e.name, VALUE)
        if e.kind == "call" and e.name.lower() in ("head", "last") and len(e.kids) == 1:
            inner = e.kids[0]
            if inner.kind == "call" and inner.name.lower() == "collect" and inner.kids:
                return self.element_type(inner.kids[0], env)
            return self.element_of(self.type_of(inner, env))
        if e.kind == "call" and e.name.lower() == "collect" and e.kids:
            t = self.element_type(e.kids[0], env)
            return VarType("list", t.labels, t.part, t.anchored, t.created) if t.kind in ("node", "rel") else VALUE
        if e.kind == "index" and e.name == "":
            t = self.type_of(e.kids[0], env)
            return self.element_of(t)
        return VALUE

    def element_type(self, e: E, env: dict[str, VarType]) -> VarType:
        """The graph type of a collected expression: the expression's own type, or that of the single graph
        variable it mentions (CASE WHEN … THEN v END)."""
        t = self.type_of(e, env)
        if t.kind in ("node", "rel"):
            return t
        graph_vars = {n for n in _var_names(e) if env.get(n, VALUE).kind in ("node", "rel")}
        return env[graph_vars.pop()] if len(graph_vars) == 1 else VALUE

    @staticmethod
    def element_of(t: VarType) -> VarType:
        if t.kind != "list":
            return VALUE
        return VarType("rel" if _looks_rel(t.labels) else "node", t.labels, t.part, t.anchored, t.created)

    def visit(self, e: E, env: dict[str, VarType], record=None) -> None:
        record = record or self.read
        if e.kind == "prop":
            base = e.kids[0]
            t = self.type_of(base, env)
            if t.kind in ("node", "rel"):
                for label in sorted(t.labels):
                    record(f"{label}.{e.name}")
            else:
                self.visit(base, env, record)
            return
        if e.kind == "label":
            for label in e.name.split(","):
                record(label)
            return
        if e.kind == "call":
            self.f.calls.add(e.name.lower())
        if e.kind == "comp":
            src = e.kids[0]
            self.visit(src, env, record)
            inner = dict(env)
            inner[e.local] = self.element_of(self.type_of(src, env)) if self.type_of(src, env).kind == "list" else VALUE
            if e.name.startswith("reduce:"):
                inner[e.name.split(":", 1)[1]] = VALUE
            for kid in e.kids[1:]:
                self.visit(kid, inner, record)
            return
        for kid in e.kids:
            self.visit(kid, env, record)

    # ── clauses
    def run(self, clauses: list[Clause], env: dict[str, VarType], top: bool) -> dict[str, VarType]:
        kinds = []
        for i, c in enumerate(clauses):
            kinds.append(c.kind)
            if top and self.wrote and c.kind in READ_CLAUSES:
                self.f.reads_after_write.append(c.kind)
            if top and c.kind in WRITE_CLAUSES:
                self.wrote = True  # reads inside the first write clause already happen after a write
            env = getattr(self, "c_" + c.kind.replace(" ", "_").lower())(c, env, clauses[i + 1:])
        if top:
            self.f.clauses = tuple(kinds)
        return env

    def is_unique(self, names, keys: set[str]) -> bool:
        return any(name == label and props <= keys for name in names for label, props in self.unique)

    def bind_part(self, part: Part, env: dict[str, VarType], clause: str, where: E | None) -> dict[str, VarType]:
        """Bind one MATCH pattern part; record whether it is anchored (L3) and yields at most one row."""
        self.parts += 1
        pid = self.parts
        env = dict(env)
        anchored = False
        determined: set[int] = set()
        anchors = _where_anchors(where)
        for idx, n in enumerate(part.nodes):
            bound = n.var in env and env[n.var].kind == "node"
            labels = set(n.labels) | (set(env[n.var].labels) if bound else set())
            for label in n.labels:
                self.read(label)
            keys = {k for k, _ in n.props} | anchors.get(n.var, set())
            for k, v in n.props:
                self.visit(v, env)
                for label in sorted(labels):
                    self.read(f"{label}.{k}")
            unique = self.is_unique(labels, {k for k, _ in n.props})
            if bound or any((label, k) in self.indexed for label in labels for k in keys):
                anchored = True
            if bound or unique:
                determined.add(idx)
            if n.var and not bound:
                env[n.var] = VarType("node", frozenset(labels), pid, unique)
        rel_unique = []
        for r in part.rels:
            for t in r.types:
                self.read(t)
            keys = {k for k, _ in r.props}
            for k, v in r.props:
                self.visit(v, env)
                for t in r.types:
                    self.read(f"{t}.{k}")
            # a fid (<pid>#<n>) is unique across the graph (§3.3), whatever the relationship type
            unique = not r.var_length and ("fid" in keys or self.is_unique(r.types, keys))
            rel_unique.append(unique)
            if any((t, k) in self.indexed for t in r.types for k in keys):
                anchored = True
            if r.var and not r.var_length:
                env[r.var] = VarType("rel", frozenset(r.types), pid, unique)
        changed = True
        while changed:
            changed = False
            for idx, unique in enumerate(rel_unique):
                if unique and (idx in determined) != (idx + 1 in determined):
                    determined |= {idx, idx + 1}
                    changed = True
        single = len(determined) == len(part.nodes) and all(rel_unique)
        self.f.matches.append(MatchInfo(clause, _describe(part), anchored, single))
        if not single:
            self.multi = True
        return env

    def c_match(self, c: Clause, env, rest):
        where = rest[0].exprs[0] if rest and rest[0].kind == "WHERE" else None
        for part in c.parts:
            env = self.bind_part(part, env, c.kind, where)
        return env

    c_optional_match = c_match

    def c_where(self, c: Clause, env, rest):
        self.visit(c.exprs[0], env)
        self.f.where_names |= _var_names(c.exprs[0])
        return env

    def c_unwind(self, c: Clause, env, rest):
        e, alias = c.items[0]
        self.visit(e, env)
        env = dict(env)
        env[alias] = self.element_of(self.type_of(e, env))
        self.multi = True
        return env

    def c_with(self, c: Clause, env, rest):
        new: dict[str, VarType] = {}
        aggregated = False
        counts: dict[int, str] = {}
        for e, alias in c.items:
            if e.kind == "star":
                new.update(env)
                continue
            self.visit(e, env)
            if _has_aggregate(e):
                aggregated = True
            if e.kind == "call" and e.name.lower() == "count" and e.kids and e.kids[0].kind == "var":
                t = env.get(e.kids[0].name, VALUE)
                if t.part >= 0:
                    counts[t.part] = alias
            new[alias] = self.type_of(e, env) if alias not in KCTX_VARS else VarType("kernel")
        for e, alias in c.items:
            pick = _positional_pick(e)
            if pick:
                t = env.get(pick, VALUE)
                self.f.picks.append(Pick(alias, pick, counts.get(t.part), t.anchored))
        for e in c.exprs:
            self.visit(e, env)
        if aggregated:
            self.multi = False
        return new

    c_return = c_with

    def new_group(self, members: set[str]) -> int:
        self.next_group += 1
        self.groups[self.next_group] = members
        return self.next_group

    def create_part(self, part: Part, env: dict[str, VarType], merge: bool) -> dict[str, VarType]:
        """CREATE / MERGE one pattern part. Elements created together (a node and the edges from it) share a
        creation group, which L1 and the matrix use to accept an identity check as the guard of a creation."""
        env = dict(env)
        node_groups: list[int] = []
        for n in part.nodes:
            existing = n.var in env and env[n.var].kind == "node" and not n.labels
            if existing:
                node_groups.append(env[n.var].created)
                continue
            if merge:
                for label in n.labels:
                    self.read(label)
                for k, v in n.props:
                    self.visit(v, env)
                    for label in n.labels:
                        self.read(f"{label}.{k}")
            else:
                for _, v in n.props:
                    self.visit(v, env)
            gid = self.new_group(set(n.labels))
            for label in n.labels:
                self.write(label)
            node_groups.append(gid)
            if n.var:
                env[n.var] = VarType("node", frozenset(n.labels), created=gid)
        for idx, r in enumerate(part.rels):
            for _, v in r.props:
                self.visit(v, env)
            ends = [g for g in (node_groups[idx], node_groups[idx + 1]) if g >= 0]
            if ends:
                gid = ends[0]
                for other in ends[1:]:
                    self.groups[gid] |= self.groups.pop(other, set())
            else:
                gid = self.new_group(set())
            self.groups[gid] |= set(r.types)
            for t in r.types:
                self.write(t)
                if merge:
                    self.read(t)
            if r.var:
                env[r.var] = VarType("rel", frozenset(r.types), created=gid)
        return env

    def c_create(self, c: Clause, env, rest):
        for part in c.parts:
            env = self.create_part(part, env, merge=False)
        return env

    def c_merge(self, c: Clause, env, rest):
        for part in c.parts:
            env = self.create_part(part, env, merge=True)
        self.set_items(c.sets, env)
        return env

    def set_items(self, items, env) -> None:
        written: set[str] = set()
        read: set[str] = set()
        for op, target, value in items:
            if op == "label":
                for label in target.name.split(","):
                    self.write(label)
                continue
            self.visit(value, env, record=lambda el: (read.add(el), self.read(el)))
            if target.kind == "prop":
                t = self.type_of(target.kids[0], env)
                for label in sorted(t.labels):
                    written.add(f"{label}.{target.name}")
            else:
                t = self.type_of(target, env)
                written |= set(t.labels)
        for element in written:
            self.write(element)
        self.f.set_conflicts |= written & read

    def c_set(self, c: Clause, env, rest):
        self.set_items(c.sets, env)
        return env

    def c_delete(self, c: Clause, env, rest):
        for e in c.exprs:
            t = self.type_of(e, env)
            for label in t.labels:
                self.write(label)
        return env

    def c_remove(self, c: Clause, env, rest):
        for e in c.exprs:
            if e.kind == "prop":
                t = self.type_of(e.kids[0], env)
                for label in t.labels:
                    self.write(f"{label}.{e.name}")
            elif e.kind == "label":
                for label in e.name.split(","):
                    self.write(label)
        return env

    def c_foreach(self, c: Clause, env, rest):
        src = c.exprs[0]
        self.visit(src, env)
        inner = dict(env)
        t = self.type_of(src, env)
        if t.kind == "list":
            inner[c.var] = self.element_of(t)
        else:
            inner[c.var] = self.element_type(src, env)
            if inner[c.var].kind == "list":
                inner[c.var] = self.element_of(inner[c.var])
        self.run(c.body, inner, top=False)
        return env


def _looks_rel(labels: frozenset) -> bool:
    return bool(labels) and all(label.isupper() for label in labels)


def _var_names(e: E) -> set[str]:
    if e.kind == "var":
        return {e.name}
    names: set[str] = set()
    for kid in e.kids:
        names |= _var_names(kid)
    if e.kind == "comp":
        names.discard(e.local)
    return names


def _has_aggregate(e: E) -> bool:
    if e.kind == "call" and e.name.lower() in AGGREGATES:
        return True
    return any(_has_aggregate(k) for k in e.kids)


def _positional_pick(e: E) -> str | None:
    """head(collect(v)), last(collect(v)) or collect(v)[i] → v."""
    if e.kind == "call" and e.name.lower() in ("head", "last") and e.kids:
        inner = e.kids[0]
    elif e.kind == "index" and e.name == "" and e.kids:
        inner = e.kids[0]
    else:
        return None
    if inner.kind == "call" and inner.name.lower() == "collect" and inner.kids:
        names = _var_names(inner.kids[0])
        return next(iter(names)) if len(names) == 1 else "?"
    return None


def _where_anchors(where: E | None) -> dict[str, set[str]]:
    """var → properties compared with `=` or `IN` at the top level of a WHERE conjunction."""
    out: dict[str, set[str]] = {}

    def walk(e: E) -> None:
        if e.kind == "op" and e.name == "AND":
            for k in e.kids:
                walk(k)
        elif e.kind == "op" and e.name in ("=", "IN"):
            left = e.kids[0]
            if left.kind == "prop" and left.kids[0].kind == "var":
                out.setdefault(left.kids[0].name, set()).add(left.name)

    if where is not None:
        walk(where)
    return out


def _describe(part: Part) -> str:
    def node(n: NodePat) -> str:
        return f"({n.var}{''.join(':' + l for l in n.labels)})"

    text = node(part.nodes[0])
    for r, n in zip(part.rels, part.nodes[1:]):
        text += f"-[{r.var}{''.join(':' + t for t in r.types)}]-" + node(n)
    return text


def _catalogue() -> tuple[set[tuple[str, str]], set[tuple[str, frozenset]]]:
    from gnomon.bootstrap.ddl import BASE_CONSTRAINTS, BASE_INDEXES

    indexed = {(i.label, i.prop) for i in BASE_INDEXES}
    unique = {(c.label, frozenset(c.props)) for c in BASE_CONSTRAINTS if c.kind == "unique"}
    # Entity kinds: `id` is unique by construction (uid = '<kind>:' + id is constrained).
    unique |= {(label, frozenset({"id"})) for label in ("Agent", "Skill", "Team", "Service", "Deploy", "Alert",
                                                         "Incident", "Task", "Message", "ExternalService")}
    # Projection edges share the fid of the fact version they mirror.
    unique |= {(rel, frozenset({"fid"})) for rel in ("DEP_NOW", "OWNER_NOW", "CONSUMES_NOW")}
    return indexed, unique


def extract(body: str) -> Footprint:
    indexed, unique = _catalogue()
    analyzer = _Analyzer(indexed, unique)
    env = {name: VarType("kernel") for name in KCTX_VARS}
    analyzer.run(parse(body), env, top=True)
    f = analyzer.f
    f.created = {frozenset(g) for g in analyzer.groups.values() if g}
    f.may_end_with_many_rows = analyzer.multi
    return f

