// Spike S1 fixture: UDF values used inside write queries.
function aMap() {
  return { a: 1, b: [1, 2], c: 'x' };
}

function aList() {
  return [1, 'two', null];
}

function isOne(v) {
  return v === 1;
}

function label(node) {
  return node.labels[0];
}

falkor.register('aMap', aMap);
falkor.register('aList', aList);
falkor.register('isOne', isOne);
falkor.register('label', label);
