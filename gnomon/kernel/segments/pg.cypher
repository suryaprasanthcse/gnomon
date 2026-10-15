// ── PG · genesis prelude (R0 only) ─────────────────────────────────────────
// Every validation runs before the first write. Each check is its own WITH … WHERE stage, so a later
// check (which may call a UDF) is only evaluated once the earlier ones have passed.
OPTIONAL MATCH (h0:Head)
WITH count(h0) AS heads
WHERE heads = 0
WITH 1 AS ok
WHERE $rule = 'Genesis' AND any(r IN $p.rules WHERE r.name = 'Genesis' AND r.sha = $rule_sha)
WITH ok
WHERE CASE WHEN $p.kernel_src IS NULL THEN false ELSE Kernel.sha256($p.kernel_src) = $p.kernel_sha END
WITH ok
WHERE size($p.agents) >= 1
  AND any(a IN $p.agents WHERE a.role = 'kernel')
  AND all(a IN $p.agents WHERE all(s IN a.skills WHERE s IN $p.skills))
WITH ok
WHERE all(r IN $p.rules WHERE CASE WHEN r.def_json IS NULL THEN false ELSE Kernel.sha256(r.def_json) = r.sha END)
WITH [] AS causes, Kernel.tick(0, 0, timestamp(), $mode, $rec_pt, $rec_lc) AS c
CREATE (h:Head {tenant: $p.tenant, seq: 0, pt: 0, lc: 0, hash: $ZERO64, kernel_sha: $p.kernel_sha,
                sys_anchor: $p.sys_anchor})
WITH h, causes, 1 AS nseq, c.pt AS now, c.lc AS now_lc
