// -- P3 - federation ack (prelude = P3; the tick is T3) -------------------------------------------
// The receipt that comes back is bound to its attached text, and in live mode the receiver's clock may be
// at most MAX_SKEW_MS ahead of the local wall clock.
WITH h, rule, causes, c
WHERE Kernel.sha256($attach) = $p.attach_sha
  AND ($mode <> 'live' OR $m.r_pt <= timestamp() + $MAX_SKEW_MS)
