"""The measured scorer Opt.queryCost (master §16.9): its sha is the hash of the analyzer, parser and cost-model source."""

from __future__ import annotations

import hashlib
from pathlib import Path

NAME = "Opt.queryCost"
SOURCES = ("shapes.py", "plan.py", "detect.py", "cost.py")


def source() -> str:
    here = Path(__file__).resolve().parent
    return "".join(f"=== {name} ===\n{(here / name).read_bytes().decode('utf-8').replace(chr(13) + chr(10), chr(10))}"
                   for name in SOURCES)


def sha() -> str:
    return hashlib.sha256(source().encode("utf-8")).hexdigest()
