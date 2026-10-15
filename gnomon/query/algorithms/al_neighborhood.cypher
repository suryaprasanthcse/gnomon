// AL-neighborhood - BFS over current dependencies (downstream: algo.bfs is outgoing-only, D-5)
MATCH (s:Service {id: $svc})
CALL algo.bfs(s, $depth, 'DEP_NOW') YIELD nodes, edges
RETURN [n IN nodes | n.uid] AS nodes, [e IN edges | e.fid] AS fids
