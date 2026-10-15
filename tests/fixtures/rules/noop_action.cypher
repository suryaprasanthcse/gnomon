// BODY - NoopAction@1 (test only): one ActionMark per mark id, anchored on the indexed Entity.uid (lint L3).
OPTIONAL MATCH (ex:Entity {uid: 'mark:' + $p.mark})
WITH KCTX, count(ex) AS n
WHERE n = 0
CREATE (:ActionMark:Entity {uid: 'mark:' + $p.mark, id: $p.mark, rw: $pid})
WITH KCTX
