// AL-rank-critical - PageRank: score flows from dependents to the services they depend on
CALL algo.pageRank('Service', 'DEP_NOW') YIELD node, score
RETURN node.id AS service, score
ORDER BY score DESC
LIMIT $k
