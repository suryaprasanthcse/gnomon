// RD-facts - Live facts touching an entity      view: {rel}:{uid}:* and {rel}:*:{uid} for every registered fact type
MATCH (h:Head)
MATCH (e:Entity {uid: $uid})-[f]-(o:Entity)
WHERE f.te = $INF AND f.vf <= h.pt AND h.pt < f.vt
RETURN h.seq AS read_seq, type(f) AS rel, startNode(f) = e AS outgoing, o.uid AS other,
       f.fid AS fid, f.vf AS since
ORDER BY rel, fid
