// AL-communities - Label propagation communities
CALL algo.labelPropagation({nodeLabels: ['Service'], relationshipTypes: ['DEP_NOW'], maxIterations: 10})
YIELD node, communityId
RETURN communityId, collect(node.id) AS services
ORDER BY communityId
