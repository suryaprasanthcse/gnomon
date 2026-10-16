"""The integer cost model (master §16.8). Medians, MADs and scores come from recorded raw µs samples, so given the
samples they are reproducible; the samples themselves are attested. Memory is not measured (DECISIONS D-4)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from gnomon.kernel.jcs import canonicalize
from gnomon.optimizer.config import OptConfig


def median(xs: Sequence[int]) -> int:
    s = sorted(xs)
    return (s[(len(s) - 1) // 2] + s[len(s) // 2]) // 2


def mad(xs: Sequence[int]) -> int:
    m = median(xs)
    return median([abs(x - m) for x in xs])


def significant(med_b: int, mad_b: int, med_k: int, mad_k: int, kappa: int) -> bool:
    return abs(med_b - med_k) > kappa * (mad_b + mad_k)


def digest(value: object) -> str:
    return hashlib.sha256(canonicalize(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Score:
    gain_us: int
    write_us: int
    build_amort_us: int
    mem_us: int
    net_us: int

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _read_delta(base: Mapping, cand: Mapping, freq: Mapping, kappa: int) -> int:
    """Σ f_q × (med_b − med_k) over the shapes whose change is significant."""
    total = 0
    for q in sorted(base):
        if not base[q] or not cand.get(q):
            continue
        mb, mk = median(base[q]), median(cand[q])
        if significant(mb, mad(base[q]), mk, mad(cand[q]), kappa):
            total += freq.get(q, 0) * (mb - mk)
    return total


def _write_added(base: Mapping, cand: Mapping, freq: Mapping) -> int:
    """Σ f_w × max(0, med_cand − med_base): what the candidate adds to the writes."""
    return sum(freq.get(w, 0) * max(0, median(cand[w]) - median(base[w]))
               for w in sorted(base) if base[w] and cand.get(w))


def score(base_read, cand_read, read_freq, base_write, cand_write, write_freq, build_us: int, config: OptConfig) -> Score:
    gain = _read_delta(base_read, cand_read, read_freq, config.kappa)
    write = _write_added(base_write, cand_write, write_freq)
    amort = build_us // config.amort_windows
    return Score(gain, write, amort, 0, gain - write - amort)


def score_drop(with_read, without_read, read_freq, with_write, without_write, write_freq, config: OptConfig) -> Score:
    """A drop trial: the shadow without the index against the base with it. Signs reversed: the writes it saves
    count for it, the read time it costs against it."""
    loss = max(0, -_read_delta(with_read, without_read, read_freq, config.kappa))
    saving = _write_added(without_write, with_write, write_freq)
    return Score(saving, loss, 0, 0, saving - loss)


def accept(plan_changed: bool, net_us: int, margin_us: int) -> bool:
    return plan_changed and net_us >= margin_us
