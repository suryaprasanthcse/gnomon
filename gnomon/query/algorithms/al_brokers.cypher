// AL-brokers - Betweenness with a fixed seed so results are reproducible
CALL algo.betweenness({nodeLabels: ['Service'], relationshipTypes: ['DEP_NOW'],
                       samplingSize: $sampling, samplingSeed: 7})
YIELD node, score
RETURN node.id AS service, score
ORDER BY score DESC
LIMIT $k
