// BODY - NoopAction@1 (test only): one ActionMark per mark id.
OPTIONAL MATCH (ex:ActionMark {id: $p.mark})
WITH KCTX, count(ex) AS n
WHERE n = 0
CREATE (:ActionMark {id: $p.mark, rw: $pid})
WITH KCTX
