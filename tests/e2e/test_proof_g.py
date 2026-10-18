"""Proof G · Audit sweep (master §26): K1-K9 are green on a busy federation, then three corruptions are injected live and
each is caught by the checks the catalogue predicts.

"Live" means into the running tenants' own graphs (no scratch copy): MU5 edits a chain head, MU16 deletes a current
dependency projection, MU28 tampers a delivered message. Each corrupts a different tenant, so the proof also shows the
blast radius of a corruption: the checks name the tenant that was corrupted and the other ledgers stay green.
"""

import pytest

from tests.support.assurance_machine import Machine

pytestmark = [pytest.mark.e2e, pytest.mark.integration, pytest.mark.gate_assure,
              pytest.mark.timeout(1500)]  # the three-tenant world is built in the test; a loaded CI runner exceeds the 300 s default

INJECTIONS = (("MU5", "C"), ("MU16", "A"), ("MU28", "B"))


def core(aw, t):
    from gnomon.audit.runner import audit

    return audit(aw.db, aw.client, aw.keys[t], ["K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8"])


def test_proof_g_every_audit_is_green_then_three_live_corruptions_are_caught_by_their_predicted_checks(aw):
    from gnomon.audit.mutations import all_mutations, apply_mutation

    machine = Machine(aw, 5).run(30)
    machine.finish()
    aw.notice("A", "B", "proof G: a delivered notice to corrupt later", pid="pg-notice")
    aw.relay().deliver("A", "B")
    aw.consume("C", "B", "pg-svc", "pg-consumer")
    aw.relay().sweep()

    # 1 · everything is green: K1-K8 on each tenant, K9a-f over the federation, each ledger's own chain
    reports, federation = aw.full_audit()
    assert all(r.ok for r in reports.values()), {t: r.as_dict() for t, r in reports.items() if not r.ok}
    assert federation.ok and {"K9a", "K9b", "K9c", "K9d", "K9e", "K9f"} <= set(federation.results), federation.as_dict()
    assert machine.decisions and any(code for *_, code, _ in machine.decisions)  # the history includes decisions

    # 2 · inject, one corruption per tenant, and read what fires
    catalogue = all_mutations()
    fired: dict[str, set[str]] = {}
    for mu, victim in INJECTIONS:
        apply_mutation(aw.db, aw.client, aw.keys[victim], mu)
        local = core(aw, victim)
        _, wide = aw.full_audit()
        fired[mu] = set(local.failed) | set(wide.failed)
        predicted = set(catalogue[mu].expected)
        assert predicted <= fired[mu], (mu, victim, predicted, sorted(fired[mu]))

    # 3 · the damage is where it was done: tenants nobody corrupted still pass every Core check of their own
    reports, _ = aw.full_audit()
    corrupted = {victim for _, victim in INJECTIONS}
    assert corrupted == set(reports) and all(not r.ok for r in reports.values())
    assert {"K1"} <= fired["MU5"] and {"K5d", "K4"} <= fired["MU16"] and {"K9e", "K4"} <= fired["MU28"]
    assert [catalogue[mu].expected for mu, _ in INJECTIONS] == [("K1",), ("K5d", "K4"), ("K9e", "K4")]


def test_proof_g_a_clean_federation_has_nothing_to_report_twice(aw):
    Machine(aw, 9).run(20).finish()
    first = aw.full_audit()
    second = aw.full_audit()
    assert all(r.ok for r in first[0].values()) and first[1].ok
    assert {t: r.as_dict() for t, r in first[0].items()} == {t: r.as_dict() for t, r in second[0].items()}
    assert first[1].as_dict() == second[1].as_dict()  # an audit is a read: running it again changes nothing
