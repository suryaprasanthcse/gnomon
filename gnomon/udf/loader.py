"""Loads the Kernel UDF library (master §8) and reports its sha.

The library sha is SHA-256 over the code the server holds, read back with
`GRAPH.UDF LIST <library> WITHCODE`, so it names what actually runs (audit K3 re-hashes the same text).

Loading is idempotent: code already on the server unchanged is left alone, and only changed code is
sent with `LOAD REPLACE`. Replacing a library while queries call it can make those calls fail with
"Unknown function" (platform fact S22), so call `load` at startup, never under traffic.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import redis

KERNEL_LIBRARY = "Kernel"
KERNEL_SOURCE = Path(__file__).resolve().parents[2] / "udf" / "kernel.js"


class UdfLoadError(RuntimeError):
    """The server refused a library's source."""


def source(path: Path = KERNEL_SOURCE) -> str:
    """The library source with newlines normalised to LF, so the sha does not depend on the checkout."""
    return path.read_text(encoding="utf-8")


def _sha(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def listed_code(client: redis.Redis, library: str = KERNEL_LIBRARY) -> str | None:
    """The code the server holds for `library`, or None when it is not loaded."""
    rows = client.execute_command("GRAPH.UDF", "LIST", library, "WITHCODE")
    for row in rows:
        fields = dict(zip(row[::2], row[1::2]))
        if fields.get("library_name") == library:
            code = fields["library_code"]
            return code.decode("utf-8") if isinstance(code, bytes) else code
    return None


def library_sha(client: redis.Redis, library: str = KERNEL_LIBRARY) -> str:
    """SHA-256 of the loaded library's code; LookupError when it is not loaded."""
    code = listed_code(client, library)
    if code is None:
        raise LookupError(f"UDF library {library!r} is not loaded")
    return _sha(code)


def load(client: redis.Redis, library: str = KERNEL_LIBRARY, code: str | None = None) -> str:
    """Ensures `library` holds `code` (default: udf/kernel.js) and returns the library sha."""
    code = source() if code is None else code
    current = listed_code(client, library)
    if current != code:
        command = ("GRAPH.UDF", "LOAD", library, code) if current is None else ("GRAPH.UDF", "LOAD", "REPLACE", library, code)
        try:
            client.execute_command(*command)
        except redis.ResponseError as exc:
            # Another process may have loaded the same code between our LIST and LOAD.
            if listed_code(client, library) != code:
                raise UdfLoadError(f"loading UDF library {library!r} failed: {exc}") from exc
    return library_sha(client, library)
