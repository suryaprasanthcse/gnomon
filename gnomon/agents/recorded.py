"""Recorded model responses (master §10.3 rule 5): replayable demos, disclosed.

LLM calls are logged by prompt hash. `RecordingLLM` wraps a model and stores each response; `RecordedLLM` answers
only from a store and fails on a prompt it has never seen, so a replay can never invent a response. `PolicyLLM` is
the deterministic stand-in "model" that produced the committed recordings: each role's policy reads the same JSON
prompt a real model would and answers with the same JSON decision. It is rule-based, not a language model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from gnomon.agents import comms, investigator, triage


class MissingRecording(KeyError):
    """A prompt with no recorded response."""


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class RecordingLLM:
    def __init__(self, inner, store: dict) -> None:
        self.inner, self.store = inner, store
        self.calls = 0
        self.misses = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        key = prompt_hash(prompt)
        if key not in self.store:
            self.misses += 1
            self.store[key] = self.inner.complete(prompt)
        return self.store[key]


class RecordedLLM:
    def __init__(self, store: dict, meta: dict | None = None) -> None:
        self.store, self.meta = store, meta or {}
        self.calls = 0
        self.misses = 0

    def complete(self, prompt: str) -> str:
        key = prompt_hash(prompt)
        if key not in self.store:
            raise MissingRecording(f"no recorded response for prompt {key[:12]}")
        self.calls += 1
        return self.store[key]

    @classmethod
    def load(cls, path: Path | str) -> RecordedLLM:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(dict(data["responses"]), dict(data.get("meta", {})))

    @staticmethod
    def dump(path: Path | str, store: dict, meta: dict | None = None) -> None:
        text = json.dumps({"meta": meta or {}, "responses": store}, sort_keys=True, indent=1)
        Path(path).write_text(text + "\n", encoding="utf-8", newline="\n")


POLICIES = {"triage": triage.policy, "investigator": investigator.policy, "comms": comms.policy}


class PolicyLLM:
    """The deterministic stand-in model: answers each role's prompt with that role's policy decision."""

    def complete(self, prompt: str) -> str:
        context = json.loads(prompt)
        return json.dumps(POLICIES[context["role"]](context), sort_keys=True)
