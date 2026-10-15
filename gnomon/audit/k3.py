"""K3 · rules and kernel (master §12.3, invariant I4).

1 every rewrite used a rule registered and active at its seq (§12.3 query);
2 every Rule's sha recomputes from its stored fields (JCS of the §5.1 definition);
3 every Rule was written by the Genesis or RegisterRule rewrite at its registered_seq (no out-of-band rules);
4 sha256(Kernel library as the server lists it + composer + segments) = Head.kernel_sha.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import redis

from gnomon.audit import CheckResult
from gnomon.audit.replay import rule_from_node
from gnomon.kernel import composer
from gnomon.udf import loader

PIN_QUERY = """MATCH (r:Rewrite)
OPTIONAL MATCH (ru:Rule {sha: r.rule_sha})
WITH r, ru
WHERE ru IS NULL OR ru.name <> r.rule OR ru.registered_seq > r.seq
   OR (ru.deactivated_seq IS NOT NULL AND ru.deactivated_seq <= r.seq)
RETURN r.seq AS seq, r.rule AS rule, r.rule_sha AS sha
ORDER BY seq"""
REGISTRARS = ("Genesis", "RegisterRule")


def kernel_source(udf_code: str) -> str:
    """kernel_src as composer.kernel_source builds it, with the library code the server actually runs."""
    files = [
        ("udf/kernel.js", udf_code),
        ("gnomon/kernel/composer.py", Path(composer.__file__).read_text(encoding="utf-8")),
        *((f"gnomon/kernel/segments/{name.lower()}.cypher", composer.segment(name)) for name in composer.INSTALLED),
    ]
    return "".join(f"=== {name} ===\n{text}" for name, text in files)


def check(graph, client: redis.Redis) -> CheckResult:
    findings: list[dict] = []
    for seq, rule, sha in graph.ro_query(PIN_QUERY).result_set:
        findings.append({"problem": "rule_not_active", "seq": seq, "rule": rule, "sha": sha})

    writers = {rid: (seq, rule) for rid, seq, rule in
               graph.ro_query("MATCH (w:Rewrite) RETURN w.id, w.seq, w.rule").result_set}
    for (props,) in graph.ro_query("MATCH (ru:Rule) RETURN properties(ru) ORDER BY ru.sha").result_set:
        sha, name = props.get("sha"), props.get("name")
        try:
            recomputed = rule_from_node(props).sha
        except (KeyError, TypeError, ValueError) as exc:
            recomputed = f"unreadable: {exc}"
        if recomputed != sha:
            findings.append({"problem": "rule_sha", "rule": name, "sha": sha, "recomputed": recomputed})
        writer = writers.get(props.get("rw"))
        if writer is None or writer[1] not in REGISTRARS or writer[0] != props.get("registered_seq"):
            findings.append({"problem": "rule_provenance", "rule": name, "sha": sha, "rw": props.get("rw")})

    head = graph.ro_query("MATCH (h:Head) RETURN h.kernel_sha").result_set
    code = loader.listed_code(client)
    if code is None:
        findings.append({"problem": "kernel_library_missing"})
    else:
        live = hashlib.sha256(kernel_source(code).encode("utf-8")).hexdigest()
        if not head or head[0][0] != live:
            findings.append({"problem": "kernel_sha", "head": head[0][0] if head else None, "live": live})
    return CheckResult("K3", findings)
