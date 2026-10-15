"""Kernel.tick (master §2.2, §2.3, §8.3): live rules, recorded modes and named errors."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1a]

LC_MAX = 1048575
WALL = 1791000000000

TICK = "WITH Kernel.tick($pt, $lc, $wall, $mode, $rec_pt, $rec_lc) AS c RETURN c.pt, c.lc"


def tick(kq, pt, lc, wall, mode, rec_pt=None, rec_lc=None):
    return kq(TICK, pt=pt, lc=lc, wall=wall, mode=mode, rec_pt=rec_pt, rec_lc=rec_lc)


@pytest.mark.parametrize(
    ("pt", "lc", "wall", "expected"),
    [
        (WALL, 5, WALL + 7, [WALL + 7, 0]),   # wall ahead: pt advances, lc resets
        (WALL, 5, WALL, [WALL, 6]),           # wall equal: lc increments
        (WALL, 5, WALL - 30, [WALL, 6]),      # wall behind: pt holds, lc increments
        (0, 0, 0, [0, 1]),
        (WALL, LC_MAX - 1, WALL, [WALL, LC_MAX]),
        (WALL, LC_MAX, WALL + 1, [WALL + 1, 0]),  # a full counter is fine once the wall moves on
    ],
    ids=["pt-advance", "lc-increment-equal", "lc-increment-behind", "origin", "lc-max", "lc-reset-at-max"],
)
def test_live_rules(kq, pt, lc, wall, expected):
    assert tick(kq, pt, lc, wall, "live") == expected


def test_live_ignores_recorded_values(kq):
    assert tick(kq, WALL, 5, WALL + 1, "live", WALL + 99, 9) == [WALL + 1, 0]


@pytest.mark.parametrize(
    ("rec_pt", "rec_lc"),
    [(WALL + 50, 0), (WALL + 50, 17), (WALL, 6), (WALL, 900)],
    ids=["later-pt", "later-pt-any-lc", "same-pt-next-lc", "same-pt-larger-lc"],
)
def test_replay_returns_recorded(kq, rec_pt, rec_lc):
    assert tick(kq, WALL, 5, None, "replay", rec_pt, rec_lc) == [rec_pt, rec_lc]


@pytest.mark.parametrize(("rec_pt", "rec_lc"), [(WALL + 50, 0), (WALL, 6)], ids=["later-pt", "same-pt-next-lc"])
def test_sim_returns_recorded(kq, rec_pt, rec_lc):
    assert tick(kq, WALL, 5, None, "sim", rec_pt, rec_lc) == [rec_pt, rec_lc]


def test_recorded_modes_never_read_the_wall(kq):
    # A wall far ahead (or a garbage wall) must not change a replay or sim result.
    assert tick(kq, WALL, 5, WALL + 10**9, "replay", WALL, 6) == [WALL, 6]
    assert tick(kq, WALL, 5, "not-a-wall", "sim", WALL, 6) == [WALL, 6]


@pytest.mark.parametrize("mode", ["replay", "sim"])
@pytest.mark.parametrize(
    ("rec_pt", "rec_lc"),
    [(WALL, 5), (WALL, 4), (WALL - 1, 99), (0, 0)],
    ids=["equal", "same-pt-lower-lc", "earlier-pt", "origin"],
)
def test_hlc_regression_throws(kq, mode, rec_pt, rec_lc):
    with pytest.raises(Exception, match="HLC_REGRESSION"):
        tick(kq, WALL, 5, None, mode, rec_pt, rec_lc)


def test_lc_overflow_throws(kq):
    with pytest.raises(Exception, match="LC_OVERFLOW"):
        tick(kq, WALL, LC_MAX, WALL, "live")
    with pytest.raises(Exception, match="LC_OVERFLOW"):
        tick(kq, WALL, LC_MAX, WALL - 5, "live")
    with pytest.raises(Exception, match="LC_OVERFLOW"):
        tick(kq, WALL, 5, None, "replay", WALL, LC_MAX + 1)


@pytest.mark.parametrize("mode", ["LIVE", "", "Replay", "simulate", None, 1])
def test_bad_mode_throws(kq, mode):
    with pytest.raises(Exception, match="BAD_MODE"):
        tick(kq, WALL, 5, WALL, mode, WALL + 1, 0)


@pytest.mark.parametrize(
    ("pt", "lc", "wall", "mode", "rec_pt", "rec_lc"),
    [
        (None, 5, WALL, "live", None, None),
        (WALL, None, WALL, "live", None, None),
        (WALL, 5, None, "live", None, None),
        (WALL, 5, 1.5, "live", None, None),
        (WALL, "5", WALL, "live", None, None),
        (WALL, 5, None, "replay", None, 6),
        (WALL, 5, None, "sim", WALL + 1, None),
        (WALL, 5, None, "sim", WALL + 0.5, 0),
        (WALL, -1, WALL, "live", None, None),
        (2**53 + 1, 0, WALL, "live", None, None),
    ],
    ids=["pt-null", "lc-null", "wall-null", "wall-float", "lc-string", "rec-pt-null", "rec-lc-null", "rec-pt-float",
         "lc-negative", "pt-unsafe"],
)
def test_requires_integers(kq, pt, lc, wall, mode, rec_pt, rec_lc):
    with pytest.raises(Exception, match="HLC_NOT_INT"):
        tick(kq, pt, lc, wall, mode, rec_pt, rec_lc)
