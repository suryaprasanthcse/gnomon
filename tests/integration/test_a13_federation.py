"""A13 · gate TD · Multi-tenant federation (master §17, §6.5, §21.2 MU26-MU31).

One file, against the real FalkorDB (contract C5). Three tenants A, B and C, each a complete kernel graph with its own
log and chain, plus the registry graph `t:_sys`. Every cross-tenant effect is a message that the receiver applies by a
rewrite in its own log; the relay holds no state of its own. The world is built once per worker (sys S0, S1 for three
tenants, S2 for three peerings, three tenant R0 with their sys anchors, T-D activated by M3 and M6), then copied per test.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_td]

ROOT = Path(__file__).resolve().parents[2]
EPOCH = 1_791_000_000_000
TENANTS = ("A", "B", "C")
PAIRS = [("A", "B"), ("A", "C"), ("B", "C")]
OPERATOR, RELAY, TRIAGE, INGEST = "ops-1", "relay-1", "tri-1", "ing-1"
KINDS = ["notice", "health", "subscribe", "unsubscribe"]
SKILLS = ["ops", "triage"]
AGENTS = [
    {"id": "kernel", "role": "kernel", "skills": []},
    {"id": OPERATOR, "role": "operator", "skills": ["ops", "triage"]},
    {"id": TRIAGE, "role": "triage", "skills": ["triage"]},
    {"id": INGEST, "role": "ingest", "skills": []},
]
MAX_SKEW_MS = 60_000


def peering(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def guard(out) -> str:
    """The label of the guard that refused a proposal (`G<k>:<label>`)."""
    assert out.code == "REJECTED_GUARD", out
    return out.detail["guard"].split(":", 1)[1]


def jcs(value) -> str:
    from gnomon.kernel.jcs import canonicalize

    return canonicalize(value)


# ── the world ───────────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def fed_golden(db, redis_raw):
    """sys + A + B + C, federation activated, no channels open yet. Built once per worker."""
    from gnomon.bootstrap.genesis import bootstrap
    from gnomon.federation import channels, sys as fsys
    from gnomon.kernel import composer
    from gnomon.kernel.registry import load_registry
    from gnomon.udf import loader

    loader.load(redis_raw)
    uid = uuid.uuid4().hex[:8]
    keys = {"sys": f"t:test-fedg{uid}-sys", **{t: f"t:test-fedg{uid}-{t}" for t in TENANTS}}
    try:
        out = fsys.bootstrap_sys(db, redis_raw, keys["sys"], mode="replay", rec_pt=EPOCH, rec_lc=0)
        assert out.code == "APPLIED", out
        sysk = fsys.sys_handle(db, keys["sys"])
        pt = EPOCH
        for t in TENANTS:
            pt += 10
            out = sysk.submit("RegisterTenant", {"id": t, "kernel_sha": composer.kernel_sha()}, pid=f"s1-{t}",
                              agent=OPERATOR, mode="replay", rec_pt=pt, rec_lc=0)
            assert out.code == "APPLIED", out
        for a, b in PAIRS:
            pt += 10
            out = sysk.submit("PeerTenants", {"a": a, "b": b, "kinds": KINDS}, pid=f"s2-{a}-{b}", agent=OPERATOR,
                              mode="replay", rec_pt=pt, rec_lc=0)
            assert out.code == "APPLIED", out
        handles = {}
        for t in TENANTS:
            anchor = fsys.anchor_of(sysk, t)
            out = bootstrap(db.select_graph(keys[t]), redis_raw, load_registry(), tenant=t, agents=AGENTS,
                            skills=SKILLS, pid="genesis", mode="replay", rec_pt=EPOCH + 1_000, rec_lc=0,
                            sys_anchor=fsys.anchor_param(anchor))
            assert out.code == "APPLIED", (t, out)
            handles[t] = channels.attach(db.select_graph(keys[t]))
            channels.activate(handles[t], operator=OPERATOR, relay=RELAY, at=EPOCH + 2_000)
        yield keys
    finally:
        for name in redis_raw.execute_command("GRAPH.LIST"):
            if name.startswith(f"t:test-fedg{uid}-"):
                redis_raw.execute_command("GRAPH.DELETE", name)


@dataclass
class Fed:
    db: object
    client: object
    keys: dict
    k: dict = field(default_factory=dict)
    sysk: object = None
    clock: int = EPOCH + 100_000
    n: itertools.count = field(default_factory=lambda: itertools.count(1))

    def pid(self, prefix: str) -> str:
        return f"{prefix}-{next(self.n)}"

    def sub(self, t, rule, p=None, agent=OPERATOR, *, pid=None, view_keys=(), read_seq=None, at=None):
        head = self.k[t].head()
        pt = at if at is not None else max(self.clock, head["pt"]) + 10
        return self.k[t].submit(rule, p or {}, pid=pid or self.pid(rule.split(".")[0].lower()), agent=agent,
                                read_seq=read_seq, view_keys=view_keys, mode="replay", rec_pt=pt, rec_lc=0)

    def ok(self, t, rule, p=None, agent=OPERATOR, **kw):
        out = self.sub(t, rule, p, agent, **kw)
        assert out.code == "APPLIED", (t, rule, p, out)
        return out

    def rows(self, t, cypher, **params):
        return self.k[t].read(cypher, params)

    def poke(self, t, cypher, **params):
        self.k[t].graph.query(cypher, params)

    def write_keys(self, t, pid):
        return self.rows(t, "MATCH (r:Rewrite {id: $pid}) RETURN r.write_keys", pid=pid)[0][0]

    def graphs(self) -> dict:
        return {t: self.k[t].graph for t in TENANTS}

    def relay(self, mode="replay", **kw):
        from gnomon.federation.relay_protocol import Relay

        return Relay(self.k, agent=RELAY, mode=mode, **kw)

    def open_all(self):
        for a, b in itertools.permutations(TENANTS, 2):
            self.ok(a, "OpenChannel", {"dir": "out", "peer": b, "sys_peering": peering(a, b)})
            self.ok(a, "OpenChannel", {"dir": "in", "peer": b, "sys_peering": peering(a, b)})

    def xmsgs(self, t, where="true"):
        return self.rows(t, f"MATCH (x:XMsg) WHERE {where} RETURN x.id, x.chan, x.chan_seq, x.kind, x.state "
                            "ORDER BY x.chan, x.chan_seq")

    def notice(self, a, b, body="degraded", incident="i-1", pid=None):
        return self.ok(a, "SendNotice", {"peer": b, "incident": incident, "body": body}, TRIAGE,
                       pid=pid or self.pid("notice"))

    def service(self, t, sid, **kw):
        if not self.rows(t, "MATCH (s:Service {id: $id}) RETURN s.id", id=sid):
            self.ok(t, "RegisterEntity.Service", {"id": sid, "name": kw.get("name", sid), "tier": "t1",
                                                  "criticality": 3, "user_facing": True})

    def consume(self, consumer, provider, svc, cons_svc, hard=True):
        """The consumer's dependency on a provider's service, and its X5 subscription (not yet delivered)."""
        ext = f"{provider}.{svc}"
        self.service(provider, svc)
        self.service(consumer, cons_svc)
        self.ok(consumer, "RegisterEntity.ExternalService",
                {"id": ext, "tenant": provider, "remote_id": svc, "contract_criticality": 3})
        self.ok(consumer, "AssertFact.DEPENDS_ON", {"s": f"svc:{cons_svc}", "o": f"ext:{ext}", "vf": EPOCH,
                                                    "supersede": False, "hard": hard})
        return self.ok(consumer, "SendSubscription", {"consumer_svc": cons_svc, "ext": ext, "peer": provider},
                       RELAY, pid=self.pid("sub"))

    def health(self, t, svc, state):
        self.clock += 100
        return self.ok(t, "Observe.health", {"source_id": self.pid("h"), "service": svc, "health": state,
                                             "observed_pt": self.clock}, INGEST)

    def audit(self, checks=None):
        from gnomon.audit.runner import audit_federation

        return audit_federation(self.db, self.client, {t: self.k[t].graph.name for t in TENANTS},
                                self.sysk.graph.name, checks)


@pytest.fixture
def fed(db, redis_raw, fed_golden):
    from gnomon.federation import channels, sys as fsys

    suffix = uuid.uuid4().hex[:8]
    copies = {name: f"t:test-fed{suffix}-{name}" for name in fed_golden}
    for name, golden in fed_golden.items():
        redis_raw.execute_command("GRAPH.COPY", golden, copies[name])
    world = Fed(db, redis_raw, copies)
    world.sysk = fsys.sys_handle(db, copies["sys"])
    for t in TENANTS:
        world.k[t] = channels.attach(db.select_graph(copies[t]))
    try:
        yield world
    finally:
        for name in redis_raw.execute_command("GRAPH.LIST"):
            if name.startswith(f"t:test-fed{suffix}-"):
                redis_raw.execute_command("GRAPH.DELETE", name)


@pytest.fixture
def linked(fed):
    """Channels open on every ordered pair, B consuming A's pay-db and C consuming it too, subscriptions delivered."""
    fed.open_all()
    fed.consume("B", "A", "pay-db", "checkout")
    fed.consume("C", "A", "pay-db", "billing")
    relay = fed.relay()
    relay.deliver("B", "A")
    relay.deliver("C", "A")
    return fed, relay


# ── composer and UDF safety ─────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_p2_and_p3_are_installed_and_ticked_with_recv():
    from gnomon.kernel import composer
    from gnomon.kernel.registry import layer_rules

    rules = {r.name: r for r in layer_rules("TD")}
    recv = rules["ReceiveMessage.health"]
    ack = rules["AckMessage"]
    assert composer.plan(recv) == ["P0", "P2", "BODY", "Q0", "RET"]
    assert composer.plan(ack) == ["P0", "P3", "BODY", "Q0", "RET"]
    t2 = composer.compose(recv)
    t3 = composer.compose(ack)
    assert "Kernel.recv(h.pt, h.lc, timestamp(), $m.s_pt, $m.s_lc, $mode, $rec_pt, $rec_lc)" in t2
    assert "Kernel.recv(h.pt, h.lc, timestamp(), $m.r_pt, $m.r_lc, $mode, $rec_pt, $rec_lc)" in t3
    for text in (t2, t3):
        assert "<TICK>" not in text and text.count("Kernel.recv(") == 1 and "Kernel.tick(" not in text
    assert "Kernel.digest($m.fields) = $m.digest" in t2 and "$MAX_SKEW_MS" in t2 and "$MAX_SKEW_MS" in t3
    assert composer.segment("P2") in composer.kernel_source() and composer.segment("P3") in composer.kernel_source()


@pytest.mark.unit
def test_federation_adds_no_global_udf_and_every_script_stays_an_iife():
    udf_files = sorted(p.name for p in (ROOT / "udf").glob("*.js"))
    assert udf_files == ["exec.js", "kernel.js"]  # federation uses Kernel.digest and Kernel.recv; it adds no library
    text = (ROOT / "udf" / "exec.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//")).lstrip()
    assert re.match(r"^;?\(\s*(function\s*\(|\(\)\s*=>|async)", code), "udf/exec.js must stay one IIFE (D-51)"
    for path in (ROOT / "gnomon" / "federation").glob("*"):
        assert path.suffix != ".js", f"{path.name}: federation helpers are Python, not global UDF code"


def test_message_params_are_bound_from_m_and_attach_is_its_canonical_json(fed):
    from gnomon.kernel.registry import layer_rules
    from gnomon.kernel.service import Proposal, bind

    rule = {r.name: r for r in layer_rules("TD")}["ReceiveMessage.notice"]
    m = {"id": "A/n", "origin_tenant": "A", "chan_seq": 1, "kind": "notice"}
    params = bind(rule, Proposal(rule=rule.name, p={"attach_sha": "x"}, pid="rx:A/n", agent=RELAY, read_seq=0, m=m,
                                 mode="replay", rec_pt=EPOCH + 1, rec_lc=0))
    assert params["m"] == m and params["attach"] == jcs(m)
    assert params["write_keys"] == ["Channel:in:A", "Outbox:rx:A/n"]  # {m.origin_tenant} instantiates from the message
    assert params["MAX_SKEW_MS"] == MAX_SKEW_MS and params["NOTICE_MAX"] == 4000


# ── sys graph: S0, S1, S2, anchors ──────────────────────────────────────────────────────────────

def test_sys_genesis_carries_only_s1_s2_m3_m4_m6(fed):
    names = {r for (r,) in fed.sysk.read("MATCH (r:Rule) RETURN r.name")}
    assert names == {"Genesis", "RegisterTenant", "PeerTenants", "RegisterRule", "DeactivateRule", "RegisterAgent"}
    assert fed.sysk.head()["tenant"] == "_sys"
    assert fed.sysk.read("MATCH (t:Tenant) RETURN t.id ORDER BY t.id") == [["A"], ["B"], ["C"]]
    assert fed.sysk.read("MATCH (p:Peering) RETURN p.key, p.a, p.b, p.state ORDER BY p.key") == [
        ["A|B", "A", "B", "active"], ["A|C", "A", "C", "active"], ["B|C", "B", "C", "active"]]


def test_s1_register_tenant_guards(fed):
    def s1(p, pid):
        return fed.sysk.submit("RegisterTenant", p, pid=pid, agent=OPERATOR, mode="replay",
                               rec_pt=fed.clock + next(fed.n) * 10, rec_lc=0)

    assert guard(s1({"id": "A", "kernel_sha": "k"}, "s1-dup")) == "new_tenant"
    assert guard(s1({"id": "_sys", "kernel_sha": "k"}, "s1-sys")) == "not_sys"
    ok = s1({"id": "D", "kernel_sha": "k"}, "s1-d")
    assert ok.code == "APPLIED"
    assert fed.sysk.read("MATCH (t:Tenant {id: 'D'}) RETURN t.state, t.registered_seq") == [["active", ok.seq]]
    assert fed.sysk.read("MATCH (r:Rewrite {id: 's1-d'}) RETURN r.write_keys") == [[["Tenant:D", "Tenant:*"]]]
    denied = fed.sysk.submit("RegisterTenant", {"id": "E", "kernel_sha": "k"}, pid="s1-e", agent="kernel",
                             mode="replay", rec_pt=fed.clock + 5_000, rec_lc=0)
    assert denied.code == "CALLER_DENIED"


def test_s2_peer_tenants_guards(fed):
    def s2(p, pid):
        return fed.sysk.submit("PeerTenants", p, pid=pid, agent=OPERATOR, mode="replay",
                               rec_pt=fed.clock + next(fed.n) * 10, rec_lc=0)

    fed.sysk.submit("RegisterTenant", {"id": "D", "kernel_sha": "k"}, pid="s1-d", agent=OPERATOR, mode="replay",
                    rec_pt=fed.clock + 1, rec_lc=0)
    assert guard(s2({"a": "B", "b": "A", "kinds": KINDS}, "p1")) == "ordered_pair"
    assert guard(s2({"a": "A", "b": "B", "kinds": KINDS}, "p2")) == "peering_new"
    assert guard(s2({"a": "A", "b": "Z", "kinds": KINDS}, "p3")) == "tenants_exist"
    assert guard(s2({"a": "A", "b": "D", "kinds": []}, "p4")) == "kinds_nonempty"
    assert s2({"a": "A", "b": "D", "kinds": ["notice"]}, "p5").code == "APPLIED"
    assert fed.sysk.read("MATCH (p:Peering {key: 'A|D'}) RETURN p.kinds, p.state") == [[["notice"], "active"]]
    fed.sysk.graph.query("MATCH (t:Tenant {id: 'D'}) SET t.state = 'suspended'")
    assert guard(s2({"a": "B", "b": "D", "kinds": KINDS}, "p6")) == "tenants_active"


def test_tenant_r0_records_a_sys_anchor_that_k9f_verifies(fed):
    from gnomon.federation import sys as fsys

    for t in TENANTS:
        anchor = fsys.parse_anchor(fed.k[t].head()["sys_anchor"])
        assert set(anchor) == {"sys_seq", "sys_hash", "sys_rw"}
        rows = fed.sysk.read("MATCH (r:Rewrite {seq: $s}) RETURN r.id, r.hash, r.rule", {"s": anchor["sys_seq"]})
        assert rows == [[anchor["sys_rw"], anchor["sys_hash"], "RegisterTenant"]]
        assert fsys.anchor_of(fed.sysk, t) == anchor
    report = fed.audit(["K9f"])
    assert report.ok, report.as_dict()


# ── X0 · X1 ─────────────────────────────────────────────────────────────────────────────────────

def test_x0_open_channel(fed):
    out = fed.ok("A", "OpenChannel", {"dir": "out", "peer": "B", "sys_peering": "A|B"}, pid="oc1")
    assert fed.rows("A", "MATCH (c:Channel {key: 'out:B'}) RETURN c.peer, c.dir, c.next_seq, c.sys_peering") == [
        ["B", "out", 1, "A|B"]]
    assert fed.write_keys("A", "oc1") == ["Channel:out:B"]
    assert out.seq == fed.rows("A", "MATCH (r:Rewrite {id: 'oc1'}) RETURN r.seq")[0][0]
    assert guard(fed.sub("A", "OpenChannel", {"dir": "out", "peer": "B", "sys_peering": "A|B"})) == "channel_new"
    assert guard(fed.sub("A", "OpenChannel", {"dir": "sideways", "peer": "C", "sys_peering": "A|C"})) == "dir_valid"
    assert guard(fed.sub("A", "OpenChannel", {"dir": "in", "peer": "A", "sys_peering": "A|A"})) == "peer_not_self"
    assert fed.sub("A", "OpenChannel", {"dir": "in", "peer": "C", "sys_peering": "A|C"}, TRIAGE).code == "CALLER_DENIED"
    assert fed.sub("A", "OpenChannel", {"dir": "in", "peer": "C", "sys_peering": "A|C"}, RELAY).code == "APPLIED"


def test_x1_send_notice_queues_a_digested_message_and_advances_the_channel(fed):
    fed.open_all()
    fed.notice("A", "B", "db degraded", "i-1", pid="n1")
    fed.notice("A", "B", "db down", "i-1", pid="n2")
    rows = fed.rows("A", "MATCH (x:XMsg) RETURN x.id, x.chan, x.chan_seq, x.kind, x.state, x.origin_tenant, "
                         "x.origin_rw, x.incident_ref, x.body ORDER BY x.chan_seq")
    assert rows == [["A/n1", "out:B", 1, "notice", "queued", "A", "n1", "i-1", "db degraded"],
                    ["A/n2", "out:B", 2, "notice", "queued", "A", "n2", "i-1", "db down"]]
    assert fed.rows("A", "MATCH (c:Channel {key: 'out:B'}) RETURN c.next_seq") == [[3]]
    assert fed.rows("A", "MATCH (c:Channel {key: 'out:C'}) RETURN c.next_seq") == [[1]]
    assert fed.write_keys("A", "n1") == ["Channel:out:B"]
    x = fed.rows("A", "MATCH (x:XMsg {id: 'A/n1'}) RETURN x.digest, x.s_pt, x.s_lc")[0]
    again = fed.rows("A", "RETURN Kernel.digest(['notice', 'A', 'A/n1', 1, $pt, $lc, 'i-1', 'db degraded'])",
                     pt=x[1], lc=x[2])[0][0]
    assert x[0] == again


def test_x1_guards(fed):
    fed.open_all()
    assert guard(fed.sub("A", "SendNotice", {"peer": "Z", "incident": "i", "body": "x"}, TRIAGE)) == "channel_exists"
    assert guard(fed.sub("A", "SendNotice", {"peer": "B", "incident": "i", "body": "x" * 4001}, TRIAGE)) == "body_size_ok"
    assert fed.sub("A", "SendNotice", {"peer": "B", "incident": "i", "body": "x" * 4000}, TRIAGE).code == "APPLIED"
    assert fed.sub("A", "SendNotice", {"peer": "B", "incident": "i", "body": "x"}, INGEST).code == "CALLER_DENIED"


# ── the relay and X3 receive, X4 ack ────────────────────────────────────────────────────────────

def test_relay_delivers_a_notice_in_order_and_acks_it(fed):
    fed.open_all()
    fed.notice("A", "B", "first", pid="n1")
    fed.notice("A", "B", "second", pid="n2")
    report = fed.relay().deliver("A", "B")
    assert report.applied == ["A/n1", "A/n2"] and report.acked == ["A/n1", "A/n2"] and report.stopped is None
    assert fed.xmsgs("B") == [["A/n1", "in:A", 1, "notice", "received"], ["A/n2", "in:A", 2, "notice", "received"]]
    assert fed.xmsgs("A") == [["A/n1", "out:B", 1, "notice", "delivered"], ["A/n2", "out:B", 2, "notice", "delivered"]]
    assert fed.rows("B", "MATCH (c:Channel {key: 'in:A'}) RETURN c.next_seq") == [[3]]
    assert fed.write_keys("B", "rx:A/n1") == ["Channel:in:A", "Outbox:rx:A/n1"]
    assert fed.write_keys("A", "ack:A/n1") == ["XMsg:A/n1:state"]
    out = fed.rows("B", "MATCH (o:Outbox {pid: 'rx:A/n1'}) RETURN o.kind, o.target, o.payload, o.state")
    assert out == [["notice_in", "operators", "first", "pending"]]
    assert fed.rows("B", "MATCH (r:Rewrite {id: 'rx:A/n1'}) RETURN r.agent, r.rule") == [[RELAY, "ReceiveMessage.notice"]]


def test_relay_keeps_no_durable_state_and_a_new_relay_finds_nothing_to_do(fed):
    fed.open_all()
    fed.notice("A", "B", pid="n1")
    assert fed.relay().deliver("A", "B").applied == ["A/n1"]
    again = fed.relay().deliver("A", "B")  # a fresh process: its cursor is derived from XMsg.state and Channel.next_seq
    assert again.applied == [] and again.acked == [] and again.stopped is None


def test_a_crash_between_receive_and_ack_resumes_from_xmsg_states(fed):
    from gnomon.federation.relay_protocol import RelayCrash

    fed.open_all()
    fed.notice("A", "B", "one", pid="n1")
    fed.notice("A", "B", "two", pid="n2")
    first = fed.relay()
    first.crash_at = "after_receive"
    with pytest.raises(RelayCrash):
        first.deliver("A", "B")
    assert fed.xmsgs("B") == [["A/n1", "in:A", 1, "notice", "received"]]
    assert fed.xmsgs("A")[0][4] == "queued"  # the sender has not heard yet
    resumed = fed.relay().deliver("A", "B")
    assert resumed.duplicates == ["A/n1"]  # DUPLICATE is absorbed, never an error
    assert resumed.acked == ["A/n1", "A/n2"] and resumed.applied == ["A/n2"]
    assert [row[4] for row in fed.xmsgs("A")] == ["delivered", "delivered"]
    assert len(fed.xmsgs("B")) == 2
    assert fed.rows("B", "MATCH (r:Rewrite) WHERE r.id STARTS WITH 'rx:' RETURN count(r)") == [[2]]


def test_a_crash_before_the_ack_lands_leaves_exactly_one_receipt_after_two_resumes(fed):
    from gnomon.federation.relay_protocol import RelayCrash

    fed.open_all()
    fed.notice("A", "B", pid="n1")
    for _ in range(2):
        relay = fed.relay()
        relay.crash_at = "after_receive"
        with pytest.raises(RelayCrash):
            relay.deliver("A", "B")
    assert fed.rows("B", "MATCH (r:Rewrite) WHERE r.id STARTS WITH 'rx:' RETURN count(r)") == [[1]]
    assert fed.relay().deliver("A", "B").acked == ["A/n1"]


def test_duplicate_delivery_reports_duplicate_before_order_is_checked(fed):
    from gnomon.federation.relay_protocol import build_m

    fed.open_all()
    fed.notice("A", "B", pid="n1")
    fed.relay().deliver("A", "B")
    m = build_m(fed.k["A"], "A/n1")
    out = _raw_receive(fed, "B", m)  # chan_seq 1 again, but B's next_seq is 2
    assert out.code == "DUPLICATE", out
    assert fed.rows("B", "MATCH (x:XMsg) RETURN count(x)") == [[1]]


def _raw_receive(fed, b, m, *, attach_sha=None, mode="replay", pid=None, at=None):
    attach = jcs(m)
    head = fed.k[b].head()
    return fed.k[b].submit(f"ReceiveMessage.{m['kind']}", {"attach_sha": attach_sha or hashlib.sha256(
        attach.encode()).hexdigest()}, pid=pid or "rx:" + m["id"], agent=RELAY, read_seq=0, m=m, mode=mode,
        rec_pt=at if at is not None else max(head["pt"], m["s_pt"]) + 1, rec_lc=0)


def test_p2_refuses_out_of_order_corrupt_and_forged_attach(fed):
    from gnomon.federation.relay_protocol import build_m

    fed.open_all()
    fed.notice("A", "B", "one", pid="n1")
    fed.notice("A", "B", "two", pid="n2")
    second = build_m(fed.k["A"], "A/n2")
    out = _raw_receive(fed, "B", second)
    assert out.code == "OUT_OF_ORDER" and out.detail["next_seq"] == 1 and out.detail["chan_seq"] == 2, out
    first = build_m(fed.k["A"], "A/n1")
    bad = dict(first, body="tampered after the digest", fields=first["fields"][:-1] + ["tampered after the digest"])
    out = _raw_receive(fed, "B", bad)
    assert out.code == "CORRUPT" and out.detail["reason"] == "digest", out
    out = _raw_receive(fed, "B", first, attach_sha="0" * 64)
    assert out.code == "CORRUPT" and out.detail["reason"] == "attach_sha", out
    assert fed.xmsgs("B") == [] and fed.rows("B", "MATCH (c:Channel {key: 'in:A'}) RETURN c.next_seq") == [[1]]
    assert _raw_receive(fed, "B", first).code == "APPLIED"


def test_p2_without_an_open_channel_is_a_named_guard_not_a_crash(fed):
    from gnomon.federation.relay_protocol import build_m

    fed.open_all()
    fed.notice("A", "B", pid="n1")
    m = build_m(fed.k["A"], "A/n1")
    fed.poke("B", "MATCH (c:Channel {key: 'in:A'}) SET c.key = 'in:gone'")
    assert guard(_raw_receive(fed, "B", m)) == "channel_exists"


def test_p2_quarantines_a_sender_whose_clock_runs_ahead_in_live_mode(fed):
    from gnomon.federation.relay_protocol import build_m

    fed.open_all()
    ahead = int(time.time() * 1000) + 10 * 60_000
    fed.sub("A", "SendNotice", {"peer": "B", "incident": "i", "body": "from the future"}, TRIAGE, pid="n1", at=ahead)
    out = _raw_receive(fed, "B", build_m(fed.k["A"], "A/n1"), mode="live")
    assert out.code == "SKEW_QUARANTINED", out
    assert out.detail["s_pt"] == ahead and out.detail["max_skew_ms"] == MAX_SKEW_MS
    assert fed.xmsgs("B") == []
    relay = fed.relay(mode="live")
    report = relay.deliver("A", "B")
    assert report.stopped == "SKEW_QUARANTINED" and ("A", "B") in relay.quarantined
    assert fed.xmsgs("A")[0][4] == "queued" and relay.deliver("A", "B").applied == []  # stays quarantined


def test_p3_quarantines_an_ack_stamped_in_the_future(fed):
    fed.open_all()
    fed.notice("A", "B", pid="n1")
    digest = fed.rows("A", "MATCH (x:XMsg {id: 'A/n1'}) RETURN x.digest")[0][0]
    ahead = int(time.time() * 1000) + 10 * 60_000
    m = {"id": "A/n1", "digest": digest, "r_tenant": "B", "r_pt": ahead, "r_lc": 0, "r_seq": 9, "r_rw": "rx:A/n1"}
    out = fed.k["A"].submit("AckMessage", {"attach_sha": hashlib.sha256(jcs(m).encode()).hexdigest()}, pid="ack:A/n1",
                            agent=RELAY, read_seq=0, m=m, mode="live")
    assert out.code == "SKEW_QUARANTINED", out
    assert out.detail["r_pt"] == ahead and out.detail["max_skew_ms"] == MAX_SKEW_MS
    assert fed.xmsgs("A")[0][4] == "queued"


def test_x4_guards_an_ack_only_for_a_queued_message_with_the_same_digest(fed):
    fed.open_all()
    fed.notice("A", "B", pid="n1")
    digest = fed.rows("A", "MATCH (x:XMsg {id: 'A/n1'}) RETURN x.digest")[0][0]

    def ack(mid, dig, pid):
        m = {"id": mid, "digest": dig, "r_tenant": "B", "r_pt": EPOCH + 5_000, "r_lc": 0, "r_seq": 1, "r_rw": "rx:" + mid}
        return fed.k["A"].submit("AckMessage", {"attach_sha": hashlib.sha256(jcs(m).encode()).hexdigest()}, pid=pid,
                                 agent=RELAY, read_seq=0, m=m, mode="replay", rec_pt=fed.clock + 10_000 + next(fed.n), rec_lc=0)

    assert guard(ack("A/missing", digest, "ack-1")) == "message_exists"
    assert guard(ack("A/n1", "f" * 64, "ack-2")) == "digest_matches"
    assert ack("A/n1", digest, "ack-3").code == "APPLIED"
    assert fed.rows("A", "MATCH (x:XMsg {id: 'A/n1'}) RETURN x.state, x.r_pt, x.r_seq, x.r_rw, x.acked_seq IS NOT NULL") == [
        ["delivered", EPOCH + 5_000, 1, "rx:A/n1", True]]
    assert guard(ack("A/n1", digest, "ack-4")) == "queued"


# ── X2 · health, subscriptions ──────────────────────────────────────────────────────────────────

def test_subscription_flow_x5_x3_creates_the_consumer_and_its_current_fact(linked):
    fed, _ = linked
    assert fed.rows("B", "MATCH (c:Service {id: 'checkout'})-[d:DEP_NOW]->(x:ExternalService) "
                         "RETURN x.remote_id, d.subscribed_seq IS NOT NULL") == [["pay-db", True]]
    sent = fed.xmsgs("B", "x.kind = 'subscribe'")
    assert len(sent) == 1 and sent[0][1:] == ["out:A", 1, "subscribe", "delivered"]
    consumers = fed.rows("A", "MATCH (c:Consumer)-[f:CONSUMES]->(s:Service {id: 'pay-db'}) WHERE f.te = $inf "
                              "RETURN c.uid, c.tenant, c.remote_svc, c.criticality, f.hard ORDER BY c.uid",
                         inf=9007199254740991)
    assert consumers == [["cons:B/checkout", "B", "checkout", 3, True], ["cons:C/billing", "C", "billing", 3, True]]
    assert fed.rows("A", "MATCH (:Consumer)-[p:CONSUMES_NOW]->(:Service {id: 'pay-db'}) RETURN count(p)") == [[2]]
    receipt = fed.rows("A", "MATCH (r:Rewrite) WHERE r.rule = 'ReceiveMessage.subscribe' RETURN r.id ORDER BY r.seq")[0][0]
    assert fed.write_keys("A", receipt) == ["Channel:in:B", "CONSUMES:*:svc:pay-db", "CONSUMES:*", "Consumer:*"]
    audit = fed.audit()
    assert audit.ok, audit.as_dict()


def test_a_subscription_for_an_unknown_service_is_consumed_as_unroutable_not_rejected(linked):
    fed, relay = linked
    fed.consume("B", "A", "other", "checkout2")
    fed.poke("A", "MATCH (s:Service {id: 'other'}) SET s.id = 'gone'")
    report = relay.deliver("B", "A")
    assert report.stopped is None and len(report.applied) == 1  # the FIFO channel is not blocked
    states = [row[4] for row in fed.xmsgs("A", "x.kind = 'subscribe' AND x.origin_tenant = 'B'")]
    assert states == ["received", "unroutable"]
    assert fed.rows("A", "MATCH (c:Consumer {uid: 'cons:B/checkout2'}) RETURN count(c)") == [[0]]
    assert [row[4] for row in fed.xmsgs("B", "x.kind = 'subscribe'")] == ["delivered", "delivered"]


def test_an_existing_subscription_arriving_again_is_a_noop(linked):
    fed, relay = linked
    fed.k["B"].graph.query("MATCH (:Service)-[d:DEP_NOW]->(:ExternalService) REMOVE d.subscribed_seq")
    fed.ok("B", "SendSubscription", {"consumer_svc": "checkout", "ext": "A.pay-db", "peer": "A"}, RELAY, pid="sub-again")
    relay.deliver("B", "A")
    states = [row[4] for row in fed.xmsgs("A", "x.kind = 'subscribe' AND x.origin_tenant = 'B'")]
    assert states == ["received", "noop"]
    assert fed.rows("A", "MATCH (:Consumer {uid: 'cons:B/checkout'})-[f:CONSUMES]->() RETURN count(f)") == [[1]]


def test_x5_guards(fed):
    fed.open_all()
    fed.service("B", "checkout")
    fed.ok("B", "RegisterEntity.ExternalService", {"id": "A.pay-db", "tenant": "A", "remote_id": "pay-db",
                                                   "contract_criticality": 3})
    p = {"consumer_svc": "checkout", "ext": "A.pay-db", "peer": "A"}
    assert guard(fed.sub("B", "SendSubscription", p, RELAY)) == "dependency_live"
    fed.ok("B", "AssertFact.DEPENDS_ON", {"s": "svc:checkout", "o": "ext:A.pay-db", "vf": EPOCH, "supersede": False,
                                          "hard": False})
    assert guard(fed.sub("B", "SendSubscription", {**p, "peer": "C"}, RELAY)) == "peer_matches"  # the ext is A's
    fed.k["B"].graph.query("MATCH (c:Channel {key: 'out:A'}) SET c.key = 'out:gone'")
    assert guard(fed.sub("B", "SendSubscription", p, RELAY)) == "channel_exists"
    fed.k["B"].graph.query("MATCH (c:Channel {key: 'out:gone'}) SET c.key = 'out:A'")
    assert fed.sub("B", "SendSubscription", p, RELAY, pid="sub-ok").code == "APPLIED"
    assert guard(fed.sub("B", "SendSubscription", p, RELAY)) == "unsubscribed"  # already subscribed


def test_unsubscribe_x6_closes_the_current_fact_and_its_projection(linked):
    fed, relay = linked
    p = {"consumer_svc": "checkout", "ext": "A.pay-db", "peer": "A"}
    assert guard(fed.sub("B", "SendUnsubscription", p, RELAY)) == "no_live_dependency"  # still depends on it
    fid = fed.rows("B", "MATCH (:Service {id: 'checkout'})-[f:DEPENDS_ON]->() WHERE f.te = $inf RETURN f.fid",
                   inf=9007199254740991)[0][0]
    fed.k["B"].graph.query("MATCH (:Service {id: 'checkout'})-[f:DEPENDS_ON {fid: $fid}]->() "
                           "SET f.te = 1, f.vt = 1", {"fid": fid})
    fed.k["B"].graph.query("MATCH (:Service {id: 'checkout'})-[p:DEP_NOW {fid: $fid}]->() DELETE p", {"fid": fid})
    assert fed.sub("B", "SendUnsubscription", p, RELAY, pid="unsub-1").code == "APPLIED"
    assert guard(fed.sub("B", "SendUnsubscription", p, RELAY)) == "subscription_outstanding"
    relay.deliver("B", "A")
    assert fed.xmsgs("A", "x.kind = 'unsubscribe'")[0][4] == "received"
    live = fed.rows("A", "MATCH (:Consumer {uid: 'cons:B/checkout'})-[f:CONSUMES]->() WHERE f.te = $inf "
                         "RETURN f.vt, f.prev_fid IS NOT NULL", inf=9007199254740991)
    assert len(live) == 1 and live[0][0] < 9007199254740991 and live[0][1] is True  # valid time closed, history kept
    assert fed.rows("A", "MATCH (:Consumer {uid: 'cons:B/checkout'})-[p:CONSUMES_NOW]->() RETURN count(p)") == [[0]]
    assert fed.rows("A", "MATCH (:Consumer {uid: 'cons:B/checkout'})-[f:CONSUMES]->() RETURN count(f)") == [[2]]


def test_x2_publish_health_fans_out_to_every_consumer_tenant(linked):
    fed, relay = linked
    fed.health("A", "pay-db", "down")
    outs = relay.publish_health("A")
    assert [o.code for o in outs] == ["APPLIED"]
    rows = fed.xmsgs("A", "x.kind = 'health'")
    assert [r[1:4] for r in rows] == [["out:B", 1, "health"], ["out:C", 1, "health"]]
    assert all(r[4] == "queued" for r in rows)
    assert fed.rows("A", "MATCH (s:Service {id: 'pay-db'}) RETURN s.published_state") == [["down/normal"]]
    rw = fed.rows("A", "MATCH (r:Rewrite {rule: 'PublishHealth'}) RETURN r.id, r.write_keys")[0]
    assert set(rw[1]) == {"Service:pay-db:published", "Channel:out:B", "Channel:out:C"}
    assert relay.publish_health("A") == []  # nothing changed since: nothing to publish
    relay.deliver("A", "B")
    relay.deliver("A", "C")
    for t in ("B", "C"):
        assert fed.rows(t, "MATCH (x:ExternalService {remote_id: 'pay-db'}) RETURN x.health, x.remote_mode, "
                           "x.health_seq IS NOT NULL") == [["down", "normal", True]]


def test_x2_guards(linked):
    fed, relay = linked
    fed.health("A", "pay-db", "degraded")
    assert guard(fed.sub("A", "PublishHealth", {"svc": "pay-db", "peers": ["B"]}, RELAY)) == "peers_match"
    assert guard(fed.sub("A", "PublishHealth", {"svc": "pay-db", "peers": ["B", "C", "Z"]}, RELAY)) == "peers_match"
    assert guard(fed.sub("A", "PublishHealth", {"svc": "nope", "peers": ["B"]}, RELAY)) == "service_exists"
    fed.k["A"].graph.query("MATCH (c:Channel {key: 'out:C'}) SET c.key = 'out:gone'")
    assert guard(fed.sub("A", "PublishHealth", {"svc": "pay-db", "peers": ["B", "C"]}, RELAY)) == "channels_exist"
    fed.k["A"].graph.query("MATCH (c:Channel {key: 'out:gone'}) SET c.key = 'out:C'")
    assert fed.sub("A", "PublishHealth", {"svc": "pay-db", "peers": ["B", "C"]}, RELAY).code == "APPLIED"
    assert guard(fed.sub("A", "PublishHealth", {"svc": "pay-db", "peers": ["B", "C"]}, RELAY)) == "state_changed"


def test_a_subscription_racing_a_publish_makes_the_publish_stale(linked):
    fed, relay = linked
    fed.consume("B", "A", "late", "checkout3")
    relay.deliver("B", "A")
    fed.health("A", "late", "down")
    seen = fed.k["A"].head()["seq"]  # the relay read the consumer set at this position
    fed.consume("C", "A", "late", "billing2")
    relay.deliver("C", "A")  # a second consumer lands after the read
    stale = fed.sub("A", "PublishHealth", {"svc": "late", "peers": ["B"]}, RELAY, read_seq=seen,
                    view_keys=["CONSUMES:*:svc:late"])
    assert stale.code == "REJECTED_STALE", stale
    assert stale.detail["stale"][0]["key"] == "CONSUMES:*:svc:late"
    fresh = fed.sub("A", "PublishHealth", {"svc": "late", "peers": ["B", "C"]}, RELAY)
    assert fresh.code == "APPLIED"


def test_a_stale_health_message_is_consumed_as_superseded_and_an_unknown_service_as_unroutable(linked):
    fed, relay = linked
    fed.health("A", "pay-db", "down")
    relay.publish_health("A")
    relay.deliver("A", "B")
    newer = fed.rows("B", "MATCH (x:ExternalService {remote_id: 'pay-db'}) RETURN x.health_s_pt")[0][0]
    fed.k["B"].graph.query("MATCH (x:ExternalService {remote_id: 'pay-db'}) SET x.health_s_pt = $v", {"v": newer + 10 ** 9})
    fed.health("A", "pay-db", "ok")
    relay.publish_health("A")
    relay.deliver("A", "B")
    states = [r[4] for r in fed.xmsgs("B", "x.kind = 'health'")]
    assert states == ["received", "superseded"]
    assert fed.rows("B", "MATCH (x:ExternalService {remote_id: 'pay-db'}) RETURN x.health") == [["down"]]
    fed.k["A"].graph.query("MATCH (s:Service {id: 'pay-db'}) SET s.id = 'moved'")
    fed.health("A", "moved", "degraded")
    fed.k["B"].graph.query("MATCH (x:ExternalService {remote_id: 'pay-db'}) SET x.remote_id = 'elsewhere'")
    relay.publish_health("A")
    assert relay.deliver("A", "B").stopped is None
    assert [r[4] for r in fed.xmsgs("B", "x.kind = 'health'")][-1] == "unroutable"


def test_an_isolated_provider_reads_as_down_at_the_consumer(linked):
    fed, relay = linked
    fed.k["A"].graph.query("MATCH (s:Service {id: 'pay-db'}) SET s.health = 'ok', s.mode = 'isolated'")
    relay.publish_health("A")
    relay.deliver("A", "B")
    assert fed.rows("B", "MATCH (x:ExternalService {remote_id: 'pay-db'}) RETURN x.health, x.remote_mode") == [
        ["down", "isolated"]]


# ── ordering guarantees F1-F5 ───────────────────────────────────────────────────────────────────

def test_f1_fifo_without_gaps_even_when_deliveries_are_interleaved_with_sends(fed):
    fed.open_all()
    relay = fed.relay()
    for i in range(5):
        fed.notice("A", "B", f"m{i}", pid=f"n{i}")
        if i % 2:
            relay.deliver("A", "B", limit=1)
    relay.deliver("A", "B")
    seqs = [r[2] for r in fed.xmsgs("B")]
    assert seqs == [1, 2, 3, 4, 5]
    assert [r[0] for r in fed.xmsgs("B")] == [f"A/n{i}" for i in range(5)]


def test_f2_exactly_once_each_message_is_one_receipt(fed):
    fed.open_all()
    for i in range(3):
        fed.notice("A", "B", f"m{i}", pid=f"n{i}")
    relay = fed.relay()
    relay.deliver("A", "B", limit=2)
    relay.deliver("A", "B")
    relay.deliver("A", "B")
    ids = [r[0] for r in fed.rows("B", "MATCH (r:Rewrite) WHERE r.id STARTS WITH 'rx:' RETURN r.id ORDER BY r.seq")]
    assert ids == ["rx:A/n0", "rx:A/n1", "rx:A/n2"]


def test_f3_causality_in_live_mode_receiver_hlc_exceeds_sender_hlc(fed):
    fed.open_all()
    for i in range(3):
        fed.notice("A", "B", f"m{i}", pid=f"n{i}")
    report = fed.relay(mode="live").deliver("A", "B")
    assert report.stopped is None and len(report.acked) == 3
    rows = fed.rows("A", "MATCH (x:XMsg) WHERE x.state = 'delivered' RETURN x.s_pt, x.s_lc, x.r_pt, x.r_lc, x.id")
    assert len(rows) == 3
    for s_pt, s_lc, r_pt, r_lc, mid in rows:
        assert (r_pt, r_lc) > (s_pt, s_lc), mid
    rx = fed.rows("B", "MATCH (r:Rewrite {id: 'rx:A/n0'}) RETURN r.pt, r.lc, r.seq")[0]
    ax = fed.rows("A", "MATCH (x:XMsg {id: 'A/n0'}) RETURN x.r_pt, x.r_lc, x.r_seq")[0]
    assert rx == ax
    assert fed.audit(["K9d"]).ok


def test_f3_a_sender_ahead_of_the_receivers_wall_clock_still_gets_a_strictly_greater_receipt(fed):
    """The sender's pt is the largest of the three clocks: the receipt takes its pt and carries the order in lc."""
    fed.open_all()
    now = int(time.time() * 1000)
    fed.sub("B", "SendNotice", {"peer": "A", "incident": "i", "body": "x"}, TRIAGE, pid="n1", at=now + 5_000)
    assert fed.relay(mode="live").deliver("B", "A").stopped is None
    got = fed.rows("A", "MATCH (r:Rewrite {id: 'rx:B/n1'}) RETURN r.pt, r.lc")[0]
    sent = fed.rows("B", "MATCH (x:XMsg {id: 'B/n1'}) RETURN x.s_pt, x.s_lc")[0]
    assert got[0] == sent[0] and got[1] == sent[1] + 1


def test_f4_the_federated_log_linearizes_happens_before(linked):
    from gnomon.federation import fedlog

    fed, relay = linked
    fed.health("A", "pay-db", "down")
    relay.publish_health("A")
    fed.notice("B", "C", "heads up", pid="nbc")
    relay.sweep()
    entries = fedlog.merge(fed.graphs())
    keys = [(e.pt, e.lc, e.tenant, e.seq) for e in entries]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    index = {(e.tenant, e.id): n for n, e in enumerate(entries)}
    for t in TENANTS:  # per-tenant order is preserved
        seqs = [e.seq for e in entries if e.tenant == t]
        assert seqs == sorted(seqs)
    for sender, receiver in itertools.permutations(TENANTS, 2):
        for mid, origin_rw in [(r[0], r[1]) for r in fed.rows(
                receiver, "MATCH (x:XMsg) WHERE x.origin_tenant = $o RETURN x.id, x.origin_rw", o=sender)]:
            assert index[(sender, origin_rw)] < index[(receiver, "rx:" + mid)], (sender, receiver, mid)
            assert index[(receiver, "rx:" + mid)] < index[(sender, "ack:" + mid)], (sender, receiver, mid)


def test_fedlog_linearizes_happens_before_with_the_cli(linked, capsys):
    from gnomon.cli import main

    fed, relay = linked
    fed.health("A", "pay-db", "down")
    relay.publish_health("A")
    relay.sweep()
    url = f"{fed.client.connection_pool.connection_kwargs['host']}:{fed.client.connection_pool.connection_kwargs['port']}"
    code = main(["fedlog", "--url", url, "--graphs", ",".join(f"{t}={fed.k[t].graph.name}" for t in TENANTS)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["count"] == len(out["entries"]) > 10
    keys = [(e["pt"], e["lc"], e["tenant"], e["seq"]) for e in out["entries"]]
    assert keys == sorted(keys)


def test_f5_bounded_drift_a_quarantined_sender_never_drags_the_receiver_clock(fed):
    fed.open_all()
    wall = int(time.time() * 1000)
    fed.sub("A", "SendNotice", {"peer": "B", "incident": "i", "body": "future"}, TRIAGE, pid="n1", at=wall + 3_600_000)
    fed.notice("A", "C", "present", pid="n2")
    relay = fed.relay(mode="live")
    assert relay.deliver("A", "B").stopped == "SKEW_QUARANTINED"
    assert relay.deliver("A", "C").stopped is None
    for t in ("B", "C"):
        assert fed.k[t].head()["pt"] <= int(time.time() * 1000) + MAX_SKEW_MS
    assert relay.quarantined[("A", "B")]["code"] == "SKEW_QUARANTINED"


# ── tenant boundary ─────────────────────────────────────────────────────────────────────────────

def test_private_tenant_state_never_enters_a_relay_channel(linked):
    fed, relay = linked
    secret = "TOP-SECRET-LEDGER-7731"
    fed.service("A", "internal-ledger", name=secret)
    fed.ok("A", "RegisterEntity.Team", {"id": "payments", "name": "payments " + secret})
    fed.health("A", "internal-ledger", "down")
    fed.health("A", "pay-db", "degraded")
    fed.notice("A", "B", "public status line", incident="i-77")
    fed.poke("A", "MATCH (x:XMsg {kind: 'notice'}) SET x.private_note = $s, x.secret = $s", s=secret)
    relay.publish_health("A")
    relay.sweep()
    for t in ("B", "C"):
        dump = json.dumps(fed.rows(t, "MATCH (n) RETURN labels(n), properties(n)"), default=str)
        dump += json.dumps(fed.rows(t, "MATCH ()-[e]->() RETURN type(e), properties(e)"), default=str)
        assert secret not in dump and "internal-ledger" not in dump, t
    published = {r[0] for r in fed.rows("B", "MATCH (x:XMsg) WHERE x.kind = 'health' RETURN x.svc")}
    assert published == {"pay-db"}  # only a service that has consumers is ever named
    keys = set(fed.rows("B", "MATCH (x:XMsg {kind: 'notice'}) RETURN keys(x)")[0][0])
    assert "private_note" not in keys and "secret" not in keys


def test_a_tenant_cannot_write_another_tenants_channel_state_through_a_message(linked):
    from gnomon.federation.relay_protocol import build_m

    fed, _ = linked
    fed.notice("A", "B", "hello", pid="n9")
    m = build_m(fed.k["A"], "A/n9")
    forged = dict(m, origin_tenant="C")  # claims C as its origin: C's in-channel is a different FIFO
    forged["fields"] = [("C" if f == "A" else f) for f in m["fields"]]
    out = _raw_receive(fed, "B", forged)
    assert out.code == "CORRUPT", out  # the digest covers origin_tenant
    assert fed.xmsgs("B", "x.origin_tenant = 'C'") == []


# ── K9 ──────────────────────────────────────────────────────────────────────────────────────────

def _busy(linked):
    fed, relay = linked
    fed.health("A", "pay-db", "down")
    relay.publish_health("A")
    fed.notice("A", "B", "db is down", pid="n1")
    fed.notice("B", "A", "ack from B", pid="n2")
    relay.sweep()
    fed.notice("A", "C", "queued, not yet delivered", pid="n3")  # leaves one queued message in A
    return fed, relay


def test_k9_passes_on_a_busy_federation(linked):
    fed, relay = _busy(linked)
    report = fed.audit()
    assert report.ok, report.as_dict()
    assert {"K9a", "K9b", "K9c", "K9d", "K9e", "K9f"} <= set(report.results)
    assert all(f"K1@{t}" in report.results for t in TENANTS) and "K1@_sys" in report.results  # every ledger's chain
    assert fed.audit(["K9a", "K9c"]).results.keys() == {"K9a", "K9c"}
    with pytest.raises(ValueError):
        fed.audit(["K9z"])


def test_k9_each_tenant_graph_still_passes_k1_to_k6_and_replays_to_the_same_hash(linked):
    from gnomon.audit.runner import audit

    fed, relay = _busy(linked)
    for t in TENANTS:
        report = audit(fed.db, fed.client, fed.k[t].graph.name, ["K1", "K2", "K3", "K4", "K5", "K6"])
        assert report.ok, (t, report.as_dict())


def test_k9_is_idempotent_over_redelivery_and_a_second_audit_changes_nothing(linked):
    from gnomon.audit.canonical import state_hash
    from gnomon.federation.relay_protocol import build_m

    fed, relay = linked
    fed.health("A", "pay-db", "down")
    relay.publish_health("A")
    fed.notice("A", "B", "once", pid="n1")
    relay.sweep()
    before = {t: state_hash(fed.k[t].graph) for t in TENANTS}
    assert relay.sweep() == 0 and relay.sweep() == 0  # nothing queued: no rewrite is made
    for mid in [r[0] for r in fed.xmsgs("A", "x.chan = 'out:B'")]:
        assert _raw_receive(fed, "B", build_m(fed.k["A"], mid)).code == "DUPLICATE"  # cross-graph delivery is idempotent
    assert fed.audit().ok and fed.audit().ok
    assert {t: state_hash(fed.k[t].graph) for t in TENANTS} == before


EXPECTED = {"MU26": {"K9b", "K9c", "K4"}, "MU27": {"K9b", "K9c"}, "MU28": {"K9e", "K4"},
            "MU29": {"K9d", "K4"}, "MU30": {"K9a", "K4"}, "MU31": {"K9f", "K4"}}
SIDE = {"MU26": "B", "MU27": "B", "MU28": "B", "MU29": "A", "MU30": "A", "MU31": "B"}


def test_the_federation_mutation_catalogue_is_mu26_to_mu31():
    from gnomon.audit.mutations import FEDERATION_MUTATIONS, LAYER_MUTATIONS, MUTATIONS, TB_MUTATIONS

    assert list(FEDERATION_MUTATIONS) == list(EXPECTED)
    assert {mu: set(m.expected) for mu, m in FEDERATION_MUTATIONS.items()} == EXPECTED
    assert all(m.title for m in FEDERATION_MUTATIONS.values())
    assert not set(FEDERATION_MUTATIONS) & (set(MUTATIONS) | set(LAYER_MUTATIONS) | set(TB_MUTATIONS))


@pytest.mark.mutation
@pytest.mark.parametrize("mu", list(EXPECTED))
def test_each_federation_mutation_is_detected_by_its_predicted_checks(linked, mu):
    from gnomon.audit.canonical import state_hash
    from gnomon.audit.mutations import apply_mutation
    from gnomon.audit.runner import audit, audit_federation

    fed, _ = _busy(linked)
    assert fed.audit().ok
    suffix = uuid.uuid4().hex[:8]
    live = {**{t: fed.k[t].graph.name for t in TENANTS}, "sys": fed.sysk.graph.name}
    scratch = {name: f"{key}:replay:{mu.lower()}{suffix}" for name, key in live.items()}
    try:
        for name, key in scratch.items():
            fed.client.execute_command("GRAPH.COPY", live[name], key)
        assert audit_federation(fed.db, fed.client, {t: scratch[t] for t in TENANTS}, scratch["sys"]).ok
        victim = SIDE[mu]
        before = state_hash(fed.db.select_graph(scratch[victim]))
        apply_mutation(fed.db, fed.client, scratch[victim], mu)
        assert state_hash(fed.db.select_graph(scratch[victim])) != before, f"{mu} changed nothing"
        report = audit_federation(fed.db, fed.client, {t: scratch[t] for t in TENANTS}, scratch["sys"])
        replay = audit(fed.db, fed.client, scratch[victim], ["K4"])
        failed = set(report.failed) | set(replay.failed)
        assert EXPECTED[mu] <= failed, (mu, failed, report.as_dict())
    finally:
        for key in scratch.values():
            if fed.client.exists(key):
                fed.client.execute_command("GRAPH.DELETE", key)


@pytest.mark.mutation
def test_federation_mutations_refuse_a_graph_that_has_nothing_to_corrupt(fed, db, redis_raw):
    from gnomon.audit.mutations import MutationNotApplicable, apply_mutation

    for mu in ("MU26", "MU27", "MU28", "MU29", "MU30"):
        with pytest.raises(MutationNotApplicable):
            apply_mutation(db, redis_raw, fed.k["A"].graph.name, mu)
    before = fed.k["B"].head()["sys_anchor"]
    apply_mutation(db, redis_raw, fed.k["B"].graph.name, "MU31")  # MU31 needs only a Head
    assert fed.k["B"].head()["sys_anchor"] != before


# ── chaos and randomized interleavings ──────────────────────────────────────────────────────────

def test_chaos_corrupted_digest_quarantines_one_channel_and_the_others_keep_flowing(fed):
    fed.open_all()
    fed.notice("A", "B", "one", pid="n1")
    fed.notice("A", "C", "two", pid="n2")
    fed.poke("A", "MATCH (x:XMsg {id: 'A/n1'}) SET x.body = 'corrupted in storage'")
    relay = fed.relay()
    report = relay.deliver("A", "B")
    assert report.stopped == "CORRUPT" and relay.quarantined[("A", "B")]["code"] == "CORRUPT"
    assert relay.quarantined[("A", "B")]["id"] == "A/n1"
    assert relay.deliver("A", "C").applied == ["A/n2"]
    assert fed.xmsgs("B") == []
    audit = fed.audit()
    assert "K9e" in audit.failed  # K9 sees the sender's corrupted queue too: the digest does not match the body


def test_chaos_out_of_order_attempt_resyncs_from_the_receivers_next_seq(fed):
    fed.open_all()
    for i in range(3):
        fed.notice("A", "B", f"m{i}", pid=f"n{i}")
    relay = fed.relay()
    relay.deliver("A", "B", limit=1)
    fed.poke("A", "MATCH (x:XMsg {id: 'A/n1'}) SET x.state = 'delivered'")  # the sender wrongly thinks n1 is done
    report = relay.deliver("A", "B")
    assert report.stopped is None  # n2 is OUT_OF_ORDER; the relay re-derives its cursor from B and re-delivers n1
    assert [r[0] for r in fed.xmsgs("B")] == ["A/n0", "A/n1", "A/n2"]


@pytest.mark.parametrize("seed", [7, 21])
def test_random_interleavings_across_three_tenants_hold_f1_to_f5_and_audit_clean(linked, seed):
    from gnomon.audit.runner import audit
    from gnomon.federation import fedlog
    from gnomon.federation.relay_protocol import RelayCrash

    fed, relay = linked
    rng = random.Random(seed)
    fed.consume("A", "B", "b-api", "a-client")
    fed.consume("C", "B", "b-api", "c-client")
    fed.consume("A", "C", "c-api", "a-client2")
    relay.sweep()
    states = ["ok", "degraded", "down"]
    providers = {"A": "pay-db", "B": "b-api", "C": "c-api"}
    for step in range(28):
        op = rng.choice(["health", "publish", "deliver", "notice", "crash", "sweep"])
        t = rng.choice(TENANTS)
        u = rng.choice([x for x in TENANTS if x != t])
        if op == "health":
            fed.health(t, providers[t], rng.choice(states))
        elif op == "publish":
            relay.publish_health(t)
        elif op == "deliver":
            relay.deliver(t, u, limit=rng.choice([1, 2, None]))
        elif op == "notice":
            fed.notice(t, u, f"s{step}", pid=f"rn{step}")
        elif op == "crash":
            relay.crash_at = "after_receive"
            try:
                relay.deliver(t, u)
            except RelayCrash:
                pass
            finally:
                relay = fed.relay()  # a crashed (or idle) process is replaced by a fresh one with no memory
        else:
            relay.sweep()
    relay.sweep()
    report = fed.audit()
    assert report.ok, report.as_dict()
    for t in TENANTS:
        assert audit(fed.db, fed.client, fed.k[t].graph.name, ["K1", "K4", "K6"]).ok, t
    entries = fedlog.merge(fed.graphs())
    keys = [(e.pt, e.lc, e.tenant, e.seq) for e in entries]
    assert keys == sorted(keys)
    for a, b in itertools.permutations(TENANTS, 2):  # F1: gapless FIFO per channel
        seqs = [r[0] for r in fed.rows(b, "MATCH (x:XMsg {chan: $c}) RETURN x.chan_seq ORDER BY x.chan_seq", c=f"in:{a}")]
        assert seqs == list(range(1, len(seqs) + 1)), (a, b, seqs)
        sent = [r[0] for r in fed.rows(a, "MATCH (x:XMsg {chan: $c}) RETURN x.chan_seq ORDER BY x.chan_seq", c=f"out:{b}")]
        assert sent == list(range(1, len(sent) + 1)) and len(seqs) == len(sent)  # F2: all delivered, once each
    ids = [r[0] for t in TENANTS for r in fed.rows(t, "MATCH (r:Rewrite) WHERE r.id STARTS WITH 'rx:' RETURN r.id")]
    assert len(ids) == len(set(ids))


# ── cross-graph explanation ─────────────────────────────────────────────────────────────────────

def test_why_follows_a_received_message_back_across_graphs(linked):
    from gnomon.federation import fedlog

    fed, relay = linked
    fed.health("A", "pay-db", "down")
    relay.publish_health("A")
    relay.sweep()
    found = fedlog.why(fed.graphs(), "B", "ext:A.pay-db")
    assert found["entity"] == "ext:A.pay-db" and found["tenant"] == "B"
    hops = found["hops"]
    assert hops[0]["tenant"] == "B" and hops[0]["rule"] == "ReceiveMessage.health"
    senders = [h for h in hops if h["tenant"] == "A"]
    assert senders[0]["rule"] == "PublishHealth"
    assert any(h["rule"] == "Observe.health" and h["tenant"] == "A" for h in hops)  # continues A's CAUSED_BY chain
    assert fedlog.why(fed.graphs(), "B", "ext:nope") is None


# ── proof D ─────────────────────────────────────────────────────────────────────────────────────

def test_proof_d_federation_ordering_across_graphs_that_share_nothing(linked):
    """§26 row D: health fan-out to two tenants, fedlog order, a skewed sender quarantined, a duplicate absorbed, K9 green."""
    from gnomon.federation import fedlog
    from gnomon.federation.relay_protocol import build_m

    fed, relay = linked
    fed.health("A", "pay-db", "down")
    assert [o.code for o in relay.publish_health("A")] == ["APPLIED"]
    relay.deliver("A", "B")
    relay.deliver("A", "C")
    for t in ("B", "C"):
        assert fed.rows(t, "MATCH (x:ExternalService {remote_id: 'pay-db'}) RETURN x.health") == [["down"]]
    entries = fedlog.merge(fed.graphs())
    keys = [(e.pt, e.lc, e.tenant, e.seq) for e in entries]
    assert keys == sorted(keys)
    published = next(n for n, e in enumerate(entries) if e.tenant == "A" and e.rule == "PublishHealth")
    received = [n for n, e in enumerate(entries) if e.rule == "ReceiveMessage.health"]
    assert len(received) == 2 and all(n > published for n in received)
    # a duplicate delivery is absorbed
    mid = fed.xmsgs("A", "x.kind = 'health' AND x.chan = 'out:B'")[0][0]
    assert _raw_receive(fed, "B", build_m(fed.k["A"], mid)).code == "DUPLICATE"
    # a skewed sender is quarantined and does not stop the other channels
    ahead = int(time.time() * 1000) + 3_600_000
    fed.sub("C", "SendNotice", {"peer": "B", "incident": "i", "body": "skewed"}, TRIAGE, pid="skew", at=ahead)
    live = fed.relay(mode="live")
    assert live.deliver("C", "B").stopped == "SKEW_QUARANTINED"
    # K9 is green: the skewed message is still queued in C, and no receiver applied it
    report = fed.audit()
    assert report.ok, report.as_dict()
