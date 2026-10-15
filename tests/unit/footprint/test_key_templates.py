"""Key templates (master §5.4, §7.3, Appendix B grammar line): instantiation and provenance (L5)."""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g2a]


def inst(templates, p, pid="pid-1", m=None):
    from gnomon.footprint.keys import instantiate

    return instantiate(templates, p, pid, m=m)


@pytest.mark.parametrize(
    ("templates", "p", "m", "expected"),
    [
        (["Task:{p.x}:state"], {"x": "t1"}, None, ["Task:t1:state"]),
        (["Tag:{p.list[]}"], {"list": ["b", None, "a", "b"]}, None, ["Tag:b", "Tag:a"]),
        (["Item:{p.list[].id}"], {"list": [{"id": 1}, {"id": None}, {}, "x", {"id": 2}]}, None, ["Item:1", "Item:2"]),
        (["Rw:{pid}"], {}, None, ["Rw:pid-1"]),
        (["Channel:in:{m.origin_tenant}"], {}, {"origin_tenant": "acme"}, ["Channel:in:acme"]),
        (["Rule:{p.def.name}"], {"def": {"name": "X"}}, None, ["Rule:X"]),
        (["A:{p.a}:{p.b}"], {"a": "x", "b": 3}, None, ["A:x:3"]),
        (["A:{p.flag}"], {"flag": True}, None, ["A:true"]),
        (["Head", "Rule:*"], {}, None, ["Head", "Rule:*"]),
    ],
    ids=["field", "list", "list-field", "pid", "message-field", "nested", "two-fields", "bool", "literals"],
)
def test_key_templates(templates, p, m, expected):
    assert inst(templates, p, m=m) == expected


@pytest.mark.parametrize(
    ("templates", "p", "m"),
    [
        (["Task:{p.x}:state"], {}, None),
        (["Task:{p.x}:state"], {"x": None}, None),
        (["Tag:{p.list[]}"], {"list": None}, None),
        (["Tag:{p.list[]}"], {"list": []}, None),
        (["Rule:{p.def.name}"], {"def": "not-a-map"}, None),
        (["Channel:in:{m.origin}"], {}, None),
    ],
    ids=["missing", "null", "null-list", "empty-list", "path-through-scalar", "no-message"],
)
def test_null_values_yield_no_key(templates, p, m):
    assert inst(templates, p, m=m) == []


def test_deterministic_order_and_dedupe():
    templates = ["B:*", "A:{p.x}", "C:{p.list[]}", "A:{p.x}", "B:*"]
    p = {"x": "1", "list": ["z", "y", "z"]}
    first = inst(templates, p)
    assert first == ["B:*", "A:1", "C:z", "C:y"]
    assert all(inst(templates, dict(p)) == first for _ in range(5))


@pytest.mark.parametrize(
    ("template", "prelude", "problem"),
    [
        ("Task:{agent}:state", "P0", "only $p fields"),
        ("Task:{now}", "P0", "only $p fields"),
        ("Task:{p.}", "P0", "malformed"),
        ("Task:{p.x", "P0", "unbalanced"),
        ("Task:{p.a[].b[]}", "P0", "malformed"),
        ("Task:{p.a}{p.b}", "P0", "segment"),
        ("Tag:{p.a[]}:{p.b[]}", "P0", "more than one list"),
        ("Channel:{m.peer}", "P0", "P2/P3"),
        ("", "P0", "empty"),
    ],
)
def test_template_validation_reports_problems(template, prelude, problem):
    from gnomon.footprint.keys import template_problems

    problems = template_problems(template, prelude)
    assert problems and any(problem in msg for msg in problems), problems


@pytest.mark.parametrize(
    ("template", "prelude"),
    [("Task:{p.task}:state", "P0"), ("HOLDS:*:{p.task}", "P0"), ("Rw:{pid}", "P0"), ("Channel:in:{m.peer}", "P2"),
     ("Channel:out:{m.peer}", "P3"), ("Rule:{p.def.name}", "P0"), ("Head", "PG")],
)
def test_valid_templates_have_no_problems(template, prelude):
    from gnomon.footprint.keys import template_problems

    assert template_problems(template, prelude) == []


def test_instantiate_refuses_invalid_templates():
    from gnomon.footprint.keys import KeyTemplateError

    with pytest.raises(KeyTemplateError, match="key template"):
        inst(["Task:{agent}"], {})


def test_service_delegates_to_footprint_keys():
    from gnomon.footprint.keys import instantiate
    from gnomon.kernel.service import instantiate_keys

    templates = ["Noop:*", "Tag:{p.tags[]}", "Rule:{p.def.name}"]
    p = {"tags": ["a", "b"], "def": {"name": "R"}}
    assert instantiate_keys(templates, p, "pid") == instantiate(templates, p, "pid")


def test_key_root_and_coverage():
    from gnomon.footprint.keys import covers, root

    assert root("Task:{p.task}:state") == "Task"
    assert root("OWNED_BY:*:{p.o}") == "OWNED_BY"
    assert covers("Task:{p.task}:state") == "state"
    assert covers("HOLDS:*:{p.task}") == "all"
    assert covers("Rule:{p.name}") == "all"
    assert covers("Agent:*") == "phantom"
    assert covers("Head") == "literal"
