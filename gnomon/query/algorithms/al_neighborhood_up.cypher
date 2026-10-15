// AL-neighborhood (upstream) - variable-length pattern against the edge direction (D-5); __DEPTH__ is an int literal
MATCH (s:Service {id: $svc})
MATCH p = (s)<-[:DEP_NOW*1..__DEPTH__]-(x)
UNWIND relationships(p) AS r
RETURN collect(DISTINCT x.uid) AS nodes, collect(DISTINCT r.fid) AS fids
