"""S22 · Can GRAPH.COPY destinations be created while UDF libraries load? (private server)"""

import threading

import pytest
import redis

pytestmark = [pytest.mark.spike, pytest.mark.gate_g0]

COPIES = 100
ROWS = 5_000


def test_s22_copies_stay_complete_while_libraries_reload(private_falkordb, facts):
    client = private_falkordb.client
    source = private_falkordb.graph()
    source.query(f"UNWIND range(1, {ROWS}) AS i CREATE (:N {{v: i}})")
    stop = threading.Event()
    reloads = [0]
    load_errors: list[str] = []

    def reload_forever():
        loader = redis.Redis(host=private_falkordb.host, port=private_falkordb.port, decode_responses=True)
        while not stop.is_set():
            try:
                loader.execute_command(
                    "GRAPH.UDF", "LOAD", "REPLACE", "SpikeS22",
                    f"function f(){{ return {reloads[0]}; }} falkor.register('f', f);",
                )
                reloads[0] += 1
            except redis.RedisError as exc:
                load_errors.append(str(exc))
        loader.close()

    worker = threading.Thread(target=reload_forever)
    worker.start()
    copy_errors, short_copies = [], []
    try:
        for k in range(COPIES):
            target = f"{source.name}:sim:{k}"
            try:
                client.execute_command("GRAPH.COPY", source.name, target)
            except redis.RedisError as exc:
                copy_errors.append(str(exc))
                continue
            rows = private_falkordb.graph(target).query("MATCH (n:N) RETURN count(n)").result_set[0][0]
            if rows != ROWS:
                short_copies.append(rows)
    finally:
        stop.set()
        worker.join()
        for name in client.execute_command("GRAPH.LIST"):
            if name.startswith(source.name):
                client.execute_command("GRAPH.DELETE", name)
        client.execute_command("GRAPH.UDF", "DELETE", "SpikeS22")

    print(f"S22 {COPIES} copies during {reloads[0]} reloads")
    assert reloads[0] > 0
    facts.expect("S22", not copy_errors and not short_copies and not load_errors)
