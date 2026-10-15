// Spike S11 / F7 fixture: the UDF graph API (getNeighbors, getNodeById, traverse, iterate*) and writes.
function nodeId(n) {
  return n.id;
}

function attrOf(id, key) {
  var n = graph.getNodeById(id);
  return n === null || n === undefined ? null : n.attributes[key];
}

function outEdges(n, type) {
  return n.getNeighbors({ direction: 'outgoing', types: [type], returnType: 'edges' }).map(function (e) {
    return {
      fid: e.attributes.fid,
      type: e.type,
      sourceKind: typeof e.source,
      source: e.source.attributes.id,
      target: e.target.attributes.id,
      sourceHasNeighbors: typeof e.source.getNeighbors === 'function'
    };
  });
}

function inNodes(n, type) {
  return n.getNeighbors({ direction: 'incoming', types: [type], returnType: 'nodes' }).map(function (x) {
    return x.attributes.id;
  });
}

function traverseIds(n, type, distance) {
  var levels = graph.traverse([n], { direction: 'outgoing', types: [type], distance: distance });
  return levels.map(function (level) {
    return level.map(function (x) { return x.attributes.id; });
  });
}

function countAll(label, type) {
  var nodes = 0;
  var edges = 0;
  for (var x of graph.iterateNodes(label)) { nodes++; }
  for (var e of graph.iterateEdges(type)) { edges++; }
  return [nodes, edges];
}

function tryWrite(n) {
  n.attributes.id = 'changed-by-udf';
  n.extra = 1;
  return n.attributes.id;
}

falkor.register('nodeId', nodeId);
falkor.register('attrOf', attrOf);
falkor.register('outEdges', outEdges);
falkor.register('inNodes', inNodes);
falkor.register('traverseIds', traverseIds);
falkor.register('countAll', countAll);
falkor.register('tryWrite', tryWrite);
