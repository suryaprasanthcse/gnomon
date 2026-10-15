// AL-domains - Weakly connected failure domains
CALL algo.WCC({nodeLabels: ['Service'], relationshipTypes: ['DEP_NOW']})
YIELD node, componentId
RETURN componentId, collect(node.id) AS services
ORDER BY componentId
