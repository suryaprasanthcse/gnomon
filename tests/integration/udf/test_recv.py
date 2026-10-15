"""Kernel.recv (master §2.2, §8.4): the four live branches, delegation to tick in recorded modes."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1a]

LC_MAX = 1048575
W = 1791000000000

RECV = "WITH Kernel.recv($pt, $lc, $wall, $m_pt, $m_lc, $mode, $rec_pt, $rec_lc) AS c RETURN c.pt, c.lc"


def recv(kq, pt, lc, wall, m_pt, m_lc, mode="live", rec_pt=None, rec_lc=None):
    return kq(RECV, pt=pt, lc=lc, wall=wall, m_pt=m_pt, m_lc=m_lc, mode=mode, rec_pt=rec_pt, rec_lc=rec_lc)


@pytest.mark.parametrize(
    ("pt", "lc", "wall", "m_pt", "m_lc", "expected"),
    [
        # l == pt == m_pt: max(lc, m_lc) + 1
        (W, 3, W - 10, W, 7, [W, 8]),
        (W, 9, W - 10, W, 2, [W, 10]),
        (W, 4, W, W, 4, [W, 5]),            # wall ties too: still the three-way branch
        # l == pt only: lc + 1
        (W + 100, 3, W, W + 50, 9, [W + 100, 4]),
        (W + 100, 3, W + 100, W + 50, 9, [W + 100, 4]),  # wall ties pt
        # l == m_pt only: m_lc + 1
        (W, 3, W + 50, W + 100, 9, [W + 100, 10]),
        (W, 30, W + 100, W + 100, 9, [W + 100, 10]),     # wall ties m_pt
        # wall strictly ahead of both: 0
        (W, 3, W + 300, W + 150, 9, [W + 300, 0]),
    ],
    ids=["equal-m-ahead", "equal-local-ahead", "all-equal", "local-pt", "local-pt-wall-tie", "message-pt",
         "message-pt-wall-tie", "wall"],
)
def test_receive_table(kq, pt, lc, wall, m_pt, m_lc, expected):
    assert recv(kq, pt, lc, wall, m_pt, m_lc) == expected


@pytest.mark.parametrize("mode", ["replay", "sim"])
def test_recorded_modes_delegate_to_tick(kq, mode):
    assert recv(kq, W, 5, None, W + 900, 3, mode, W + 1, 0) == [W + 1, 0]
    with pytest.raises(Exception, match="HLC_REGRESSION"):
        recv(kq, W, 5, None, W + 900, 3, mode, W, 5)


def test_recorded_modes_ignore_message_fields(kq):
    # In replay the message clock was already folded into the recorded value; garbage there is not read.
    assert recv(kq, W, 5, None, None, "x", "replay", W, 6) == [W, 6]


@pytest.mark.parametrize(
    ("pt", "lc", "wall", "m_pt", "m_lc"),
    [(W, LC_MAX, W, W, 0), (W, 0, W - 1, W, LC_MAX), (W, LC_MAX, W - 1, W - 5, 0), (W - 5, 0, W - 9, W, LC_MAX)],
    ids=["three-way-local", "three-way-message", "local", "message"],
)
def test_lc_overflow_throws(kq, pt, lc, wall, m_pt, m_lc):
    with pytest.raises(Exception, match="LC_OVERFLOW"):
        recv(kq, pt, lc, wall, m_pt, m_lc)


def test_bad_mode_throws(kq):
    with pytest.raises(Exception, match="BAD_MODE"):
        recv(kq, W, 5, W, W, 5, "Live", W + 1, 0)


@pytest.mark.parametrize(
    ("pt", "lc", "wall", "m_pt", "m_lc"),
    [(W, 5, W, None, 5), (W, 5, W, W, None), (W, 5, W, W + 0.5, 5), (W, 5, None, W, 5), (None, 5, W, W, 5)],
    ids=["m-pt-null", "m-lc-null", "m-pt-float", "wall-null", "pt-null"],
)
def test_requires_integers(kq, pt, lc, wall, m_pt, m_lc):
    with pytest.raises(Exception, match="HLC_NOT_INT"):
        recv(kq, pt, lc, wall, m_pt, m_lc)
