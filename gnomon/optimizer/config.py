"""Optimizer constants (master Appendix C). θ is the ratio theta_num / theta_den, so every comparison is integer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OptConfig:
    period_ms: int = 900_000
    window_ms: int = 3_600_000
    top_n: int = 10
    param_sets: int = 3
    warmup: int = 3
    runs: int = 15
    theta_num: int = 1
    theta_den: int = 10
    kappa: int = 3
    amort_windows: int = 24
    mu_us_per_mb: int = 50
    margin_us: int = 5000
    index_budget: int = 24
    cooldown_ms: int = 3_600_000
    drop_windows: int = 6
    ddl_timeout_ms: int = 300_000
    write_cap: int = 50  # most synthesized proposals per rule and candidate (the frequency still counts the window)

    def as_dict(self) -> dict:
        return dict(self.__dict__)
