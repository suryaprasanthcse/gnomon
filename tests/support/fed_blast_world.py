"""Federated blast-radius world (A14): the Executive world of A11 with T-D switched on and scorer v2 registered.

The provider tenant is the incident-response graph of tests/support/exec_world.py with its service c degraded (not down)
so that isolating c is a real alternative to failing it over: a healthy replica c-r1 exists, a depends hard on b on c, f
depends softly on c. Other tenants subscribe to c through the real X3 rule (ReceiveMessage.subscribe), exactly as the
relay would deliver them, so a Consumer, its CONSUMES fact, its CONSUMES_NOW projection and the key versions all exist.

Hand-computed integer weights (W = 2520, a node d levels from the source weighs W / (d + 1)):
  base v1      c 2*1*2520 = 5040, b 3*1*1260 = 3780, a 3*1*840 = 2520, f 4*1*1260 = 5040                 = 16380
  consumers    acme/checkout (criticality 4, hard) 4*1*1260 = 5040, globex/login (5, hard) 5*1*1260 = 6300  = +11340
  isolate c    c, b, a go down (10080 + 7560 + 5040), f (soft) is rescued: benefit 5040, harm 11340
               + consumers down (10080 + 12600): harm +11340
  failover c   c is ok again: everything is rescued
"""

from __future__ import annotations

import hashlib

import pytest

from tests.support import exec_world
from tests.support.exec_world import XW
from tests.support.ir_world import IR

OPERATOR, RELAY = "ops-1", "relay-1"
PROVIDER = "prov"
CONSUMERS = (("acme", "checkout", 4, True), ("globex", "login", 5, True))


def build_fed(ir: IR, db, redis_raw, *, scorer_version=2, consumers=CONSUMERS, **kw) -> XW:
    from gnomon.federation import channels

    ir.clock = channels.activate(ir.k, operator=OPERATOR, relay=RELAY, at=ir.clock + 10) + 10
    kw.setdefault("c_health", "degraded")
    world = exec_world.build(ir, db, redis_raw, scorer_version=scorer_version, **kw)
    for tenant, svc, criticality, hard in consumers:
        open_channel(world, tenant)
        subscribe(world, tenant, svc, "c", criticality=criticality, hard=hard)
    return world


def open_channel(world: XW, tenant: str) -> None:
    a, b = sorted((tenant, PROVIDER))
    world.ir.ok("OpenChannel", {"dir": "in", "peer": tenant, "sys_peering": f"{a}|{b}"}, OPERATOR)


def subscribe(world: XW, tenant: str, consumer_svc: str, provider_svc: str, *, criticality=3, hard=True):
    """Delivers a subscription from `tenant` the way the relay does: ReceiveMessage.subscribe with the message as `$m`."""
    from gnomon.executive import engine
    from gnomon.federation import messages
    from gnomon.kernel.jcs import canonicalize

    ir = world.ir
    seq = ir.k.read("MATCH (c:Channel {key: $k}) RETURN c.next_seq", {"k": f"in:{tenant}"})[0][0]
    ir.clock += 10
    props = {"id": f"{tenant}/{seq}", "chan": f"out:{PROVIDER}", "chan_seq": seq, "kind": "subscribe",
             "s_pt": ir.clock, "s_lc": 0, "origin_tenant": tenant, "origin_seq": seq, "origin_rw": f"sub-{tenant}-{seq}",
             "consumer_svc": consumer_svc, "provider_svc": provider_svc, "hard": hard, "criticality": criticality}
    props["digest"] = engine.digest(messages.digest_fields(props))
    m = messages.message(props)
    ir.clock += 10
    return ir.k.submit("ReceiveMessage.subscribe", {"attach_sha": hashlib.sha256(canonicalize(m).encode()).hexdigest()},
                       pid="rx:" + props["id"], agent=RELAY, read_seq=0, m=m, mode="replay", rec_pt=ir.clock, rec_lc=0)


@pytest.fixture
def fxw(opened, db, redis_raw) -> XW:
    return build_fed(opened, db, redis_raw)
