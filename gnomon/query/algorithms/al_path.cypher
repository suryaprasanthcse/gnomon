// AL-path - Shortest dependency path, in either direction
MATCH (a:Service {id: $from}), (b:Service {id: $to})
CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: ['DEP_NOW'],
                   relDirection: 'both', maxLen: $max_len, pathCount: 1})
YIELD path
RETURN [n IN nodes(path) | n.uid] AS nodes, [r IN relationships(path) | r.fid] AS fids
