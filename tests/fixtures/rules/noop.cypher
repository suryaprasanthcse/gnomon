// ── BODY · Noop@1 (test only) ───────────────────────────────────────────────
// Guard no_blocker: no :Blocker node exists. `$p.boom = true` throws after the write (rollback tests).
OPTIONAL MATCH (b:Blocker)
WITH KCTX, count(b) AS blockers
WHERE blockers = 0
CREATE (n:Noop {pid: $pid, seq: nseq, pt: now, lc: now_lc, note: $p.note})
SET n.boom = CASE WHEN coalesce($p.boom, false) THEN Kernel.digest([1.5]) ELSE null END
WITH KCTX
