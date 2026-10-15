"""Registry meta-rules (master §9.13–§9.16): M3 RegisterRule, M4 DeactivateRule, M6 RegisterAgent, M7 RegisterEntity."""

import dataclasses

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1c]

EPOCH = 1_791_000_000_000


def guard(outcome):
    assert outcome.code == "REJECTED_GUARD", outcome
    return outcome.detail["guard"]


def m3(k, rule, *, pid, agent="ops-1", **overrides):
    from gnomon.kernel.registry import register_rule_params

    p = register_rule_params(rule)
    p.update(overrides)
    return k.submit("RegisterRule", p, pid=pid, agent=agent)


def rule_rows(k, name):
    return k.read("MATCH (r:Rule {name: $n}) RETURN r.version, r.sha, r.active, r.registered_seq, r.deactivated_seq "
                  "ORDER BY r.version", {"n": name})


# ── M3 · RegisterRule ───────────────────────────────────────────────────────

def test_m3_registers_a_new_rule(genesis):
    noop = genesis.registry.get("Noop")
    ping = dataclasses.replace(noop, name="Ping", cls="memory")
    out = m3(genesis, ping, pid="m3-ping")
    assert out.code == "APPLIED", out
    assert rule_rows(genesis, "Ping") == [[1, ping.sha, True, out.seq, None]]
    stored = genesis.read("MATCH (r:Rule {name: 'Ping'}) RETURN r.body, r.class, r.caller, r.guard_probes_json, r.rw")
    assert stored == [[ping.body, "memory", ["kernel"], ping.to_param()["guard_probes_json"], "m3-ping"]]
    assert genesis.read("MATCH (r:Rewrite {id: 'm3-ping'}) RETURN r.write_keys") == [[["Rule:Ping", "Rule:*"]]]


def test_m3_replacing_deactivates_previous_version(genesis):
    from gnomon.kernel.registry import Registry
    from gnomon.kernel.service import KernelHandle

    noop1 = genesis.registry.get("Noop")
    noop2 = dataclasses.replace(noop1, version=2)
    out = m3(genesis, noop2, pid="m3-noop2")
    assert out.code == "APPLIED", out
    assert rule_rows(genesis, "Noop") == [[1, noop1.sha, False, 1, out.seq], [2, noop2.sha, True, out.seq, None]]

    assert genesis.submit("Noop", {"note": "old"}, pid="n-old").code == "RULE_PINNED"
    rules = [noop2 if r.name == "Noop" else r for r in genesis.registry.rules()]
    k2 = KernelHandle(genesis.graph, Registry(rules))
    assert k2.submit("Noop", {"note": "new"}, pid="n-new").code == "APPLIED"


def test_m3_version_must_be_next(genesis):
    noop = genesis.registry.get("Noop")
    assert guard(m3(genesis, dataclasses.replace(noop, version=3), pid="m3-v3")) == "version_next"
    assert guard(m3(genesis, dataclasses.replace(noop, name="Fresh", version=2), pid="m3-f2")) == "version_next"


def test_m3_sha_must_match_def_json(genesis):
    ping = dataclasses.replace(genesis.registry.get("Noop"), name="Ping")
    assert guard(m3(genesis, ping, pid="m3-bad", sha="f" * 64)) == "sha_matches_def"
    assert guard(m3(genesis, ping, pid="m3-bad2", def_json=ping.def_json + " ")) == "sha_matches_def"
    assert rule_rows(genesis, "Ping") == []


def test_m3_sha_must_be_new(genesis):
    assert guard(m3(genesis, genesis.registry.get("Noop"), pid="m3-same")) == "sha_new"


def test_m3_cannot_reregister_genesis(genesis):
    g2 = dataclasses.replace(genesis.registry.get("Genesis"), version=2)
    assert guard(m3(genesis, g2, pid="m3-g2")) == "sha_matches_def"
    assert rule_rows(genesis, "Genesis")[0][2] is True


def test_m3_operator_only(genesis):
    ping = dataclasses.replace(genesis.registry.get("Noop"), name="Ping")
    assert m3(genesis, ping, pid="m3-k", agent="kernel").code == "CALLER_DENIED"


# ── M4 · DeactivateRule ─────────────────────────────────────────────────────

def test_m4_deactivates_a_memory_rule(genesis):
    out = genesis.submit("DeactivateRule", {"name": "AssertFact.MEMBER_OF", "version": 1}, pid="m4", agent="ops-1")
    assert out.code == "APPLIED", out
    assert rule_rows(genesis, "AssertFact.MEMBER_OF")[0][2:] == [False, 1, out.seq]
    later = genesis.submit("AssertFact.MEMBER_OF", {"s": "agent:ops-1", "o": "team:x", "vf": EPOCH, "supersede": False},
                           pid="mo", agent="ops-1")
    assert later.code == "RULE_PINNED"
    assert genesis.read("MATCH (r:Rewrite {id: 'm4'}) RETURN r.write_keys") == [
        [["Rule:AssertFact.MEMBER_OF", "Rule:*"]]
    ]


@pytest.mark.parametrize("name", ["Genesis", "Noop", "RegisterRule", "DeactivateRule", "RegisterAgent"])
def test_m4_protects_kernel_and_registry_rules(genesis, name):
    out = genesis.submit("DeactivateRule", {"name": name, "version": 1}, pid=f"m4-{name}", agent="ops-1")
    assert guard(out) == "active_and_not_kernel"
    assert rule_rows(genesis, name)[0][2] is True


def test_m4_rejects_inactive_or_unknown(genesis):
    p = {"name": "AssertFact.MEMBER_OF", "version": 1}
    assert genesis.submit("DeactivateRule", p, pid="m4-a", agent="ops-1").code == "APPLIED"
    assert guard(genesis.submit("DeactivateRule", p, pid="m4-b", agent="ops-1")) == "active_and_not_kernel"
    assert guard(genesis.submit("DeactivateRule", {"name": "Nope", "version": 1}, pid="m4-c", agent="ops-1")) == \
        "active_and_not_kernel"


# ── M6 · RegisterAgent ──────────────────────────────────────────────────────

def test_m6_register_and_deactivate(genesis):
    out = genesis.submit("RegisterAgent", {"op": "register", "id": "inv-1", "role": "investigator",
                                           "skills": ["triage", "ops"]}, pid="m6-r", agent="ops-1")
    assert out.code == "APPLIED", out
    assert genesis.read("MATCH (a:Agent:Entity {id: 'inv-1'})-[:HAS_SKILL]->(s:Skill) "
                        "WITH a, s ORDER BY s.id RETURN a.uid, a.role, a.active, a.rw, collect(s.id)") == [
        ["agent:inv-1", "investigator", True, "m6-r", ["ops", "triage"]]
    ]
    assert genesis.read("MATCH (r:Rewrite {id: 'm6-r'}) RETURN r.write_keys") == [[["Agent:*", "Agent:inv-1:state"]]]

    # the new investigator may now propose facts; once deactivated it may not
    genesis.submit("RegisterEntity.Team", {"id": "t9", "name": "t9"}, pid="team", agent="ops-1")
    member = {"s": "agent:inv-1", "o": "team:t9", "vf": EPOCH, "supersede": False}
    assert genesis.submit("AssertFact.MEMBER_OF", member, pid="mf1", agent="inv-1").code == "APPLIED"
    out = genesis.submit("RegisterAgent", {"op": "deactivate", "id": "inv-1"}, pid="m6-d", agent="ops-1")
    assert out.code == "APPLIED", out
    assert genesis.read("MATCH (a:Agent {id: 'inv-1'}) RETURN a.active, a.deactivated_seq") == [[False, out.seq]]
    assert genesis.submit("RetractFact.MEMBER_OF", {"s": "agent:inv-1", "o": "team:t9", "fid": "mf1#0", "mode": "void"},
                          pid="mf2", agent="inv-1").code == "CALLER_DENIED"


@pytest.mark.parametrize(
    "p",
    [
        {"op": "register", "id": "k2", "role": "kernel", "skills": []},
        {"op": "register", "id": "w1", "role": "wizard", "skills": []},
        {"op": "register", "id": "w2", "role": "triage", "skills": ["juggling"]},
        {"op": "register", "id": "ops-1", "role": "operator", "skills": []},
        {"op": "deactivate", "id": "kernel"},
        {"op": "deactivate", "id": "ghost"},
        {"op": "rename", "id": "ops-1"},
    ],
    ids=["kernel-role", "unknown-role", "unknown-skill", "duplicate-id", "deactivate-kernel", "deactivate-unknown",
         "bad-op"],
)
def test_m6_rejections(genesis, p):
    assert guard(genesis.submit("RegisterAgent", p, pid="m6-x", agent="ops-1")) == "op_valid"


def test_m6_deactivated_agent_stays_deactivated(genesis):
    assert genesis.submit("RegisterAgent", {"op": "deactivate", "id": "triage-1"}, pid="d1", agent="ops-1").applied
    assert guard(genesis.submit("RegisterAgent", {"op": "deactivate", "id": "triage-1"}, pid="d2", agent="ops-1")) == \
        "op_valid"


# ── M7 · RegisterEntity ─────────────────────────────────────────────────────

MEMBERS = [
    ("Service", "svc", {"id": "web", "name": "Web", "tier": "t0", "criticality": 5, "user_facing": True},
     {"name": "Web", "tier": "t0", "criticality": 5, "user_facing": True, "health": "ok", "mode": "normal"}),
    ("Team", "team", {"id": "sre", "name": "SRE"}, {"name": "SRE"}),
    ("Skill", "skill", {"id": "dba"}, {}),
    ("ExternalService", "ext", {"id": "pay", "tenant": "acme", "remote_id": "svc-1", "contract_criticality": 4},
     {"tenant": "acme", "remote_id": "svc-1", "contract_criticality": 4, "health": "ok", "remote_mode": "normal"}),
]


@pytest.mark.parametrize(("label", "kind", "p", "props"), MEMBERS, ids=[m[0] for m in MEMBERS])
def test_m7_registers_each_member(genesis, label, kind, p, props):
    out = genesis.submit(f"RegisterEntity.{label}", p, pid=f"m7-{label}", agent="ops-1")
    assert out.code == "APPLIED", out
    [[node]] = genesis.read(f"MATCH (n:{label}:Entity {{uid: $uid}}) RETURN properties(n)", {"uid": f"{kind}:{p['id']}"})
    assert node == {"uid": f"{kind}:{p['id']}", "id": p["id"], "rw": f"m7-{label}", **props}
    assert genesis.read("MATCH (r:Rewrite {id: $pid}) RETURN r.write_keys", {"pid": f"m7-{label}"}) == [
        [[f"{label}:*", f"{label}:{p['id']}:state"]]
    ]
    assert guard(genesis.submit(f"RegisterEntity.{label}", p, pid=f"m7-{label}-2", agent="ops-1")) == "new_uid"


def test_m7_deploy_needs_its_service(genesis):
    p = {"id": "d1", "service": "web", "version": "1.2.3", "verified": True, "expected_health": "ok"}
    assert guard(genesis.submit("RegisterEntity.Deploy", p, pid="dep-0", agent="ops-1")) == "service_exists"
    genesis.submit("RegisterEntity.Service", MEMBERS[0][2], pid="svc", agent="ops-1")
    out = genesis.submit("RegisterEntity.Deploy", p, pid="dep-1", agent="ops-1")
    assert out.code == "APPLIED", out
    assert genesis.read("MATCH (d:Deploy:Entity {uid: 'dep:d1'}) RETURN d.service, d.version, d.verified, "
                        "d.expected_health, d.rw") == [["web", "1.2.3", True, "ok", "dep-1"]]
    assert guard(genesis.submit("RegisterEntity.Deploy", p, pid="dep-2", agent="ops-1")) == "new_uid"
    bad = dict(p, id="d2", expected_health="melting")
    assert guard(genesis.submit("RegisterEntity.Deploy", bad, pid="dep-3", agent="ops-1")) == "valid"


@pytest.mark.parametrize(
    ("label", "p"),
    [
        ("Service", {"id": "s0", "name": "x", "tier": "t", "criticality": 0, "user_facing": True}),
        ("Service", {"id": "s6", "name": "x", "tier": "t", "criticality": 6, "user_facing": True}),
        ("Service", {"id": "s7", "name": "x", "tier": "t", "criticality": 3, "user_facing": "yes"}),
        ("ExternalService", {"id": "e0", "tenant": "a", "remote_id": "r", "contract_criticality": 9}),
    ],
    ids=["criticality-0", "criticality-6", "user-facing-not-bool", "contract-criticality-9"],
)
def test_m7_validates(genesis, label, p):
    assert guard(genesis.submit(f"RegisterEntity.{label}", p, pid="m7-bad", agent="ops-1")) == "valid"


def test_m7_kind_prefix_separates_labels(genesis):
    # Same id, different kinds: uids differ (svc:x vs team:x), so both register.
    assert genesis.submit("RegisterEntity.Service", dict(MEMBERS[0][2], id="x"), pid="sx", agent="ops-1").applied
    assert genesis.submit("RegisterEntity.Team", {"id": "x", "name": "X"}, pid="tx", agent="ops-1").applied


def test_m7_callers(genesis):
    assert genesis.submit("RegisterEntity.Team", {"id": "q", "name": "Q"}, pid="tq", agent="triage-1").code == \
        "CALLER_DENIED"
