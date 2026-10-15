// Test fixture only (not product code): a throwaway UDF library used by the H0 container smoke test.
function greet(name) {
  return 'hello ' + name;
}

falkor.register('greet', greet);
