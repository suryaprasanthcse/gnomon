// BODY - ApproveDecision@1 (R17): a human approves or vetoes exactly the sha that was requested, before it expires.
MATCH (ap:Approval {aid: $p.aid})
WHERE ap.state = 'pending' AND ap.decision_sha = $p.decision_sha AND now < ap.expires_pt
  AND $p.verdict IN ['approve', 'veto']
WITH KCTX, ap
SET ap.state = 'decided', ap.verdict = $p.verdict, ap.approver = $agent,
    ap.note = $p.note, ap.decided_seq = nseq
WITH KCTX
