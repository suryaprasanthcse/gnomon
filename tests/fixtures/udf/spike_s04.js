// Spike S4 fixture: a UDF that always throws, to abort an enclosing write query.
function boom() {
  throw new Error('BOOM');
}

falkor.register('boom', boom);
