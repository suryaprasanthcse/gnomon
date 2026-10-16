// -- P2 - federation receive (prelude = P2; the tick is T2) ---------------------------------------
// Runs after the tick and before anything is written. The channel must be open and at exactly this message
// (FIFO without gaps: the guard and the increment are one serialized apply), the digest must match the digest
// fields, the attached text must be the one whose hash is in the params, and in live mode the sender's clock
// may be at most MAX_SKEW_MS ahead of the local wall clock. A duplicate pid never gets here: P0 refuses it first.
MATCH (ch:Channel {key: 'in:' + $m.origin_tenant})
WHERE ch.next_seq = $m.chan_seq
  AND Kernel.digest($m.fields) = $m.digest
  AND Kernel.sha256($attach) = $p.attach_sha
  AND ($mode <> 'live' OR $m.s_pt <= timestamp() + $MAX_SKEW_MS)
WITH h, rule, causes, c
