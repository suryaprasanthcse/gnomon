"""gnomon.udf.loader: the Kernel library sha comes from GRAPH.UDF LIST <lib> WITHCODE; loading is idempotent.

Reloads are tested sequentially on a private library name, never on Kernel itself, because replacing a
library while another worker calls it can fail those calls (platform fact S22).
"""

import hashlib
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gate_g1a]

ROOT = Path(__file__).resolve().parents[3]
KERNEL_JS = ROOT / "udf" / "kernel.js"


def withcode(redis_raw, library: str) -> str:
    rows = redis_raw.execute_command("GRAPH.UDF", "LIST", library, "WITHCODE")
    assert len(rows) == 1, rows
    fields = dict(zip(rows[0][::2], rows[0][1::2]))
    assert fields["library_name"] == library
    return fields["library_code"]


@pytest.fixture
def scratch_library(redis_raw):
    name = f"LoaderTest{uuid.uuid4().hex[:8]}"
    yield name
    if redis_raw.execute_command("GRAPH.UDF", "LIST", name):
        redis_raw.execute_command("GRAPH.UDF", "DELETE", name)


def test_library_sha_stable(redis_raw, kernel_sha):
    from gnomon.udf import loader

    listed = withcode(redis_raw, "Kernel")
    assert kernel_sha == hashlib.sha256(listed.encode("utf-8")).hexdigest()
    assert loader.library_sha(redis_raw) == kernel_sha
    assert loader.library_sha(redis_raw, "Kernel") == kernel_sha
    assert listed == KERNEL_JS.read_text(encoding="utf-8")
    assert loader.source() == listed
    assert loader.load(redis_raw) == kernel_sha  # already loaded with identical code: a no-op


def test_reload_is_idempotent(db, tenant, redis_raw, scratch_library):
    from gnomon.udf import loader

    v1 = "function v() { return 1; }\nfalkor.register('v', v);\n"
    v2 = "function v() { return 2; }\nfalkor.register('v', v);\n"
    graph = db.select_graph(tenant)
    call = f"RETURN {scratch_library}.v()"

    sha1 = loader.load(redis_raw, scratch_library, v1)
    assert sha1 == hashlib.sha256(v1.encode("utf-8")).hexdigest()
    assert loader.load(redis_raw, scratch_library, v1) == sha1
    assert loader.load(redis_raw, scratch_library, v1) == sha1
    assert withcode(redis_raw, scratch_library) == v1
    assert graph.query(call).result_set == [[1]]

    sha2 = loader.load(redis_raw, scratch_library, v2)  # changed code is replaced
    assert sha2 == hashlib.sha256(v2.encode("utf-8")).hexdigest() != sha1
    assert loader.library_sha(redis_raw, scratch_library) == sha2
    assert graph.query(call).result_set == [[2]]
    assert loader.load(redis_raw, scratch_library, v2) == sha2


def test_library_sha_of_missing_library_raises(redis_raw, scratch_library):
    from gnomon.udf import loader

    with pytest.raises(LookupError, match=scratch_library):
        loader.library_sha(redis_raw, scratch_library)


def test_rejected_source_raises_and_loads_nothing(redis_raw, scratch_library):
    from gnomon.udf import loader

    with pytest.raises(loader.UdfLoadError, match=scratch_library):
        loader.load(redis_raw, scratch_library, "function broken( {")
    assert redis_raw.execute_command("GRAPH.UDF", "LIST", scratch_library) == []


def test_source_reads_udf_directory():
    from gnomon.udf import loader

    assert loader.KERNEL_SOURCE == KERNEL_JS
    assert loader.source() == KERNEL_JS.read_text(encoding="utf-8")
    assert loader.source(KERNEL_JS) == loader.source()
