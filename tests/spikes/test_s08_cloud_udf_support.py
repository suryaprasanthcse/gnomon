"""S8 · UDF support on FalkorDB Cloud.

Only an endpoint named in GNOMON_FALKORDB_CLOUD_URL (host:port, password in GNOMON_FALKORDB_CLOUD_PASSWORD)
can answer this. Without one the question stays unverified, which the facts record as FAIL so the
§23.2 fallback (Docker Compose for live) is the design in effect.
"""

import os

import pytest
import redis

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]


def _cloud_supports_udfs(url: str, password: str | None) -> bool:
    host, _, port = url.rpartition(":")
    client = redis.Redis(host=host, port=int(port), password=password, decode_responses=True, socket_timeout=10)
    try:
        client.execute_command("GRAPH.UDF", "LOAD", "REPLACE", "SpikeS08", "function f(){ return 8; } falkor.register('f', f);")
        ok = client.execute_command("GRAPH.QUERY", "spike-s08", "RETURN SpikeS08.f()")[1] == [[8]]
        client.execute_command("GRAPH.UDF", "DELETE", "SpikeS08")
        return ok
    except redis.RedisError:
        return False
    finally:
        client.close()


def test_s08_udf_support_on_falkordb_cloud(redis_raw, facts):
    libraries = redis_raw.execute_command("GRAPH.UDF", "LIST")
    assert isinstance(libraries, list)  # the pinned local image serves GRAPH.UDF

    url = os.environ.get("GNOMON_FALKORDB_CLOUD_URL")
    cloud_verified = bool(url) and _cloud_supports_udfs(url, os.environ.get("GNOMON_FALKORDB_CLOUD_PASSWORD"))
    facts.expect("S8", cloud_verified)
