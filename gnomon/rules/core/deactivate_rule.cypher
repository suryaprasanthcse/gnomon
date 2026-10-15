// ── BODY · DeactivateRule@1 (M4) ───────────────────────────────────────────
// Kernel-class rules and the registry's own meta-rules can never be switched off.
MATCH (x:Rule {name: $p.name, version: $p.version})
WHERE x.active = true AND x.class <> 'kernel'
  AND NOT x.name IN ['RegisterRule', 'DeactivateRule', 'RegisterAgent']
WITH KCTX, x
SET x.active = false, x.deactivated_seq = nseq
WITH KCTX
