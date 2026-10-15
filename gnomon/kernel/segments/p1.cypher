// -- P1 - decision checks (rule.requires_decision = true) ---------------------------------------
// Runs after the tick and before anything is written. Every binding the kernel can check atomically is checked
// here: the pinned scorer is active, the base snapshot is the one the decision was made on, the attach is the
// envelope that was hashed, the decision sha is the one in the params, a gated rule has an approving decided
// Approval bound to this very sha, and every cite is a fact that is live at the rewrite's own time.
MATCH (sc:Scorer {name: $d.scorer, sha: $d.scorer_sha})
WHERE sc.active = true
  AND $d.read_seq = $read_seq
  AND Kernel.sha256($attach) = $p.attach_sha
  AND $p.decision_sha = $d.sha
OPTIONAL MATCH (ap:Approval {aid: $d.aid})
WITH h, rule, causes, c, ap
WHERE rule.gated = false
   OR (ap IS NOT NULL AND ap.state = 'decided' AND ap.verdict = 'approve'
       AND ap.decision_sha = $d.sha)
UNWIND (CASE WHEN size($d.cites) = 0 THEN [null] ELSE $d.cites END) AS ct
OPTIONAL MATCH (cs:Entity {uid: ct.s})-[cf]-()
WHERE cf.fid = ct.fid AND cf.te = $INF AND cf.vf <= c.pt AND c.pt < cf.vt
WITH h, rule, causes, c, count(DISTINCT cf.fid) AS live_cites
WHERE live_cites = size($d.cites)
