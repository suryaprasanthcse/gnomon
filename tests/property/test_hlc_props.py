"""HLC properties of Kernel.tick and Kernel.recv (master §8.4), checked against the real UDFs.

- outputs are strictly greater than (pt, lc);
- recv outputs are also strictly greater than (m_pt, m_lc);
- pt_out >= wall in live mode;
- replay and sim return exactly the recorded value;
- a chain of live ticks is strictly increasing however the wall moves.
"""

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

pytestmark = [pytest.mark.property, pytest.mark.gate_g1a]

LC_MAX = 1048575
W = 1791000000000
PT = st.integers(min_value=W - 10_000, max_value=W + 10_000)  # dense around one instant, so ties are common
LC = st.integers(min_value=0, max_value=LC_MAX - 1)  # strictly below LC_MAX: no overflow in one step
FIXTURES = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])

TICK = "WITH Kernel.tick($pt, $lc, $wall, $mode, $rec_pt, $rec_lc) AS c RETURN c.pt, c.lc"
RECV = "WITH Kernel.recv($pt, $lc, $wall, $m_pt, $m_lc, $mode, $rec_pt, $rec_lc) AS c RETURN c.pt, c.lc"


@pytest.fixture(scope="module")
def kernel(redis_raw):
    from gnomon.udf import loader

    return loader.load(redis_raw)


@pytest.fixture
def q(db, tenant, kernel):
    graph = db.select_graph(tenant)
    return lambda cypher, **params: tuple(graph.query(cypher, params).result_set[0])


@FIXTURES
@given(pt=PT, lc=LC, wall=PT)
def test_live_tick_advances_and_tracks_wall(q, pt, lc, wall):
    out = q(TICK, pt=pt, lc=lc, wall=wall, mode="live", rec_pt=None, rec_lc=None)
    assert out > (pt, lc)
    assert out[0] >= wall
    assert out[0] == max(pt, wall)


@FIXTURES
@given(pt=PT, lc=LC, wall=PT, m_pt=PT, m_lc=LC)
def test_live_recv_exceeds_local_and_message(q, pt, lc, wall, m_pt, m_lc):
    out = q(RECV, pt=pt, lc=lc, wall=wall, m_pt=m_pt, m_lc=m_lc, mode="live", rec_pt=None, rec_lc=None)
    assert out > (pt, lc)
    assert out > (m_pt, m_lc)
    assert out[0] >= wall
    assert out[0] == max(pt, m_pt, wall)


@FIXTURES
@given(mode=st.sampled_from(["replay", "sim"]), pt=PT, lc=LC, rec_pt=PT, rec_lc=LC, wall=st.none() | PT)
def test_recorded_modes_return_recorded(q, mode, pt, lc, rec_pt, rec_lc, wall):
    assume((rec_pt, rec_lc) > (pt, lc))
    tick = q(TICK, pt=pt, lc=lc, wall=wall, mode=mode, rec_pt=rec_pt, rec_lc=rec_lc)
    recv = q(RECV, pt=pt, lc=lc, wall=wall, m_pt=W, m_lc=0, mode=mode, rec_pt=rec_pt, rec_lc=rec_lc)
    assert tick == recv == (rec_pt, rec_lc)
    assert tick > (pt, lc)


@FIXTURES
@given(mode=st.sampled_from(["replay", "sim"]), pt=PT, lc=LC, rec_pt=PT, rec_lc=LC)
def test_recorded_regression_is_refused(q, mode, pt, lc, rec_pt, rec_lc):
    assume((rec_pt, rec_lc) <= (pt, lc))
    with pytest.raises(Exception, match="HLC_REGRESSION"):
        q(TICK, pt=pt, lc=lc, wall=None, mode=mode, rec_pt=rec_pt, rec_lc=rec_lc)


@settings(FIXTURES, max_examples=40)
@given(start=PT, steps=st.lists(st.integers(min_value=-50, max_value=50), min_size=1, max_size=40))
def test_live_chain_strictly_increases(q, start, steps):
    walls = [start]
    for step in steps:
        walls.append(walls[-1] + step)  # the wall may jump back: HLC must still advance
    rows = q(
        "WITH $walls AS walls "
        "WITH reduce(acc = [[$pt, 0]], w IN walls | "
        "  acc + [[Kernel.tick(acc[-1][0], acc[-1][1], w, 'live', null, null).pt, "
        "          Kernel.tick(acc[-1][0], acc[-1][1], w, 'live', null, null).lc]]) AS hist "
        "RETURN hist",
        walls=walls, pt=start,
    )
    hist = [tuple(h) for h in rows[0]]
    assert all(b > a for a, b in zip(hist, hist[1:]))
    assert all(h[0] >= w for h, w in zip(hist[1:], walls))
