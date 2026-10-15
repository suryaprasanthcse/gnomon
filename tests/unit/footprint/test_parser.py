"""Static extraction (master §7.4): type-level reads and writes, guard reads, created labels, calls.

Element vocabulary: `Label` / `TYPE` for nodes or relationships of that label or type (matched, created
or deleted), `Label.prop` / `TYPE.prop` for a property. Variables bound by the kernel context (KCTX) and
values derived from $parameters are not graph elements.
"""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.gate_g2a]

KCTX = "h, causes, nseq, now, now_lc"


def fp(body):
    from gnomon.footprint.parser import extract

    return extract(body)


def test_match_where_set():
    f = fp(f"MATCH (t:Task {{id: $p.task}})\nWHERE t.state = 'open'\nWITH {KCTX}, t\nSET t.state = 'claimed'\n"
           f"WITH {KCTX}")
    assert f.reads == {"Task", "Task.id", "Task.state"}
    assert f.writes == {"Task.state"}
    assert f.guard_reads == {"Task", "Task.id", "Task.state"}
    assert f.clauses == ("MATCH", "WHERE", "WITH", "SET", "WITH")


def test_create_relationship_between_bound_nodes():
    f = fp(f"MATCH (a:Agent {{id: $agent}}), (t:Task {{id: $p.task}})\nWITH {KCTX}, a, t\n"
           f"CREATE (a)-[:HOLDS {{from_seq: nseq, rw: $pid}}]->(t)\nWITH {KCTX}")
    assert f.reads == {"Agent", "Agent.id", "Task", "Task.id"}
    assert f.writes == {"HOLDS"}
    assert f.created == {frozenset({"HOLDS"})}


def test_create_multi_label_node_and_merge():
    f = fp(f"FOREACH (sk IN $p.skills |\n  CREATE (:Skill:Entity {{uid: 'skill:' + sk, id: sk}}))\n"
           f"FOREACH (sid IN $p.ids |\n  MERGE (s:Skill {{id: sid}}))\nWITH {KCTX}")
    assert f.writes == {"Skill", "Entity"}
    assert f.reads == {"Skill", "Skill.id"}
    assert frozenset({"Skill", "Entity"}) in f.created
    assert f.guard_reads == set()  # FOREACH is the first write clause


def test_aliases_through_aggregates_keep_their_type():
    body = (
        f"MATCH (s:Entity {{uid: $p.s}})\n"
        f"OPTIONAL MATCH (s)-[cur:OWNED_BY]->(oo:Entity)\nWHERE cur.te = $INF\n"
        f"WITH {KCTX}, s, count(cur) AS n, head(collect(cur)) AS old, head(collect(oo)) AS old_o\n"
        f"WHERE n = 0 OR old.vf < $p.vf\n"
        f"FOREACH (_ IN CASE WHEN old IS NULL THEN [] ELSE [1] END |\n  SET old.te = nseq)\n"
        f"WITH {KCTX}"
    )
    f = fp(body)
    assert f.reads == {"Entity", "Entity.uid", "OWNED_BY", "OWNED_BY.te", "OWNED_BY.vf"}
    assert f.writes == {"OWNED_BY.te"}
    assert "OWNED_BY.vf" in f.guard_reads


def test_foreach_variable_inherits_the_single_graph_variable_of_its_list():
    body = (
        f"MATCH (s:Entity {{uid: $p.s}})\nOPTIONAL MATCH (s)-[pn:OWNER_NOW]->()\n"
        f"WITH {KCTX}, s, count(pn) AS n_pn, head(collect(pn)) AS old_pn\nWHERE n_pn <= 1\n"
        f"FOREACH (x IN CASE WHEN old_pn IS NULL THEN [] ELSE [old_pn] END | DELETE x)\nWITH {KCTX}"
    )
    assert fp(body).writes == {"OWNER_NOW"}


def test_parameter_values_are_not_graph_reads():
    f = fp(f"WITH {KCTX}\nWHERE any(a IN $p.agents WHERE a.role = 'kernel') AND all(r IN $p.rules WHERE r.sha <> '')\n"
           f"FOREACH (r IN $p.rules | CREATE (:Rule {{name: r.name, sha: r.sha}}))\nWITH {KCTX}")
    assert f.reads == set()
    assert f.writes == {"Rule"}


def test_label_predicates_are_reads():
    f = fp(f"MATCH (s:Entity {{uid: $p.s}}), (o:Entity {{uid: $p.o}})\n"
           f"WHERE s:Service AND (o:Service OR o:ExternalService) AND o.service = s.id\nWITH {KCTX}")
    assert f.reads == {"Entity", "Entity.uid", "Service", "ExternalService", "Entity.service", "Entity.id"}


def test_relationship_pattern_properties_and_directions():
    f = fp(f"MATCH (s:Entity {{uid: $p.s}})-[cur:DEPENDS_ON {{fid: $p.fid}}]->(o:Entity)\n"
           f"MATCH (o)<-[:HAS_SKILL]-(a:Agent)\nWITH {KCTX}, cur\nSET cur.te = nseq, cur.tt = now\nWITH {KCTX}")
    assert f.reads == {"Entity", "Entity.uid", "DEPENDS_ON", "DEPENDS_ON.fid", "HAS_SKILL", "Agent"}
    assert f.writes == {"DEPENDS_ON.te", "DEPENDS_ON.tt"}


def test_calls_and_same_set_reads():
    f = fp(f"MATCH (t:Task {{id: $p.t}})\nWITH {KCTX}, t\nSET t.n = t.n + 1, t.h = Kernel.sha256(t.id), "
           f"t.at = timestamp()\nWITH {KCTX}")
    assert {"kernel.sha256", "timestamp"} <= f.calls
    assert f.set_conflicts == {"Task.n"}


def test_reads_after_first_write_are_not_guard_reads():
    f = fp(f"MATCH (t:Task {{id: $p.t}})\nWITH {KCTX}, t\nSET t.a = 1\nWITH {KCTX}, t\nWHERE t.b = 2\nWITH {KCTX}")
    assert "Task.b" in f.reads
    assert "Task.b" not in f.guard_reads
    assert f.reads_after_write == ["WHERE"]


def test_comments_and_strings():
    f = fp(f"// leading comment with MATCH (x:Nope)\nMATCH (t:Task {{id: 'a//b'}}) // trailing (y:Nope)\n"
           f"WHERE t.note = \"it's // fine\"\nWITH {KCTX}")
    assert f.reads == {"Task", "Task.id", "Task.note"}


def test_list_comprehension_and_case_and_unwind():
    f = fp(f"UNWIND $p.ids AS i\nMATCH (t:Task {{id: i}})\n"
           f"WITH {KCTX}, collect(t) AS ts, [x IN $p.ids WHERE x <> '' | toUpper(x)] AS up\n"
           f"FOREACH (t2 IN ts | SET t2.state = CASE WHEN size(up) > 0 THEN 'x' ELSE 'y' END)\nWITH {KCTX}")
    assert f.writes == {"Task.state"}
    assert f.reads == {"Task", "Task.id"}
    assert f.clauses[0] == "UNWIND"


def test_variable_length_and_anonymous_patterns():
    f = fp(f"MATCH (s:Service {{id: $p.s}})<-[:DEP_NOW*1..4]-(x)\nWITH {KCTX}, count(x) AS n\nWITH {KCTX}")
    assert f.reads == {"Service", "Service.id", "DEP_NOW"}


@pytest.mark.parametrize(
    "bad",
    ["MATCH (t:Task", "MATCH (t:Task) WHERE (t.a = 1", "FROB (x)", "MATCH (t:Task {id: })", "SET t.x =",
     "MATCH (a)-[r:R->(b)", "WITH 'unterminated"],
)
def test_parse_errors(bad):
    from gnomon.footprint.parser import ParseError

    with pytest.raises(ParseError):
        fp(bad)


def test_every_registered_body_parses():
    from gnomon.kernel.registry import load_registry

    for rule in load_registry().rules():
        f = fp(rule.body)
        assert f.writes, rule.name
