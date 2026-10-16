// Exec · the blast-radius scorer (master §15.5, §15.6, §18). Read-only; every step is integer arithmetic and every
// iteration runs in uid or fid order, so two runs on equal graphs produce byte-identical output.
//
//   Exec.blastRadius(seed, hops, cfg) -> { version, region, sources, impacted, teams, owner_edges }
//
// The graph it reads: Service nodes (uid, criticality, health, mode), DEP_NOW edges a -> b ("a depends on b", fid,
// hard) and OWNER_NOW edges to Team nodes (fid). With cfg.federation === true (scorer v2, T-E) CONSUMES_NOW edges join
// the region and the dependents: a Consumer (uid 'cons:...') is a leaf weighted by its subscribed criticality, and an
// ExternalService proxy weighs its contract_criticality. Without it the output is the v1 output, bit for bit.

// The whole library sits in one function scope: UDF libraries share the server's global scope, so a bare
// `function attr` here would replace the Kernel library's `attr` (and `edgesOf`, `fail`) for every rewrite.
(function () {
var W = 2520; // lcm(1..9): exact integer weights for hops <= 8
var SEV = { down: 2, degraded: 1 };

function fail(detail) {
  throw new Error('Exec.blastRadius: ' + detail);
}

function attr(node, key) {
  var v = node.attributes[key];
  return v === undefined ? null : v;
}

function cmp(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

function edgesOf(node, direction, types) {
  return node.getNeighbors({ direction: direction, types: types, returnType: 'edges' });
}

function isConsumer(node) {
  return String(attr(node, 'uid')).indexOf('cons:') === 0;
}

function isExternal(node) {
  return String(attr(node, 'uid')).indexOf('ext:') === 0;
}

function otherEnd(edge, node) {
  return edge.source.id === node.id ? edge.target : edge.source;
}

function byFid(a, b) {
  return cmp(attr(a, 'fid'), attr(b, 'fid'));
}

function fidOf(edge) {
  return edge.attributes.fid;
}

function state0(node) {
  return attr(node, 'mode') === 'isolated' ? 'down' : attr(node, 'health');
}

// propagate(sev_b, mode_b, hard): the state a dependent takes from a dependency, or null when nothing propagates.
function propagate(sev, mode, hard) {
  if (mode === 'isolated') {
    return hard ? { sev: 'down', soft: false } : null;
  }
  if (sev === 'down') {
    return hard ? { sev: 'down', soft: false } : { sev: 'degraded', soft: true };
  }
  if (sev === 'degraded') {
    return hard ? { sev: 'degraded', soft: false } : { sev: 'degraded', soft: true };
  }
  return null;
}

function blastRadius(seed, hops, cfg) {
  var fed = cfg !== null && cfg !== undefined && cfg.federation === true;
  var REGION_TYPES = fed ? ['DEP_NOW', 'CONSUMES_NOW'] : ['DEP_NOW'];
  var DEPENDENT_TYPES = REGION_TYPES;
  // crit(Service) = criticality; crit(Consumer) = its subscribed criticality; crit(ExternalService) = contract_criticality
  function crit(node) {
    return fed && isExternal(node) ? attr(node, 'contract_criticality') : attr(node, 'criticality');
  }
  if (!Number.isInteger(hops) || hops < 1 || hops > 8) {
    fail('hops must be an integer between 1 and 8, got ' + hops);
  }
  // 1 - region: undirected breadth-first search, deterministic order
  var nodes = {};
  var dist = {};
  nodes[attr(seed, 'uid')] = seed;
  dist[attr(seed, 'uid')] = 0;
  var frontier = [seed];
  var region = [seed];
  for (var d = 1; d <= hops; d++) {
    var next = [];
    frontier.sort(function (a, b) { return cmp(attr(a, 'uid'), attr(b, 'uid')); });
    for (var fi = 0; fi < frontier.length; fi++) {
      var n = frontier[fi];
      var edges = edgesOf(n, 'outgoing', REGION_TYPES).concat(edgesOf(n, 'incoming', REGION_TYPES)).sort(byFid);
      for (var ei = 0; ei < edges.length; ei++) {
        var m = otherEnd(edges[ei], n);
        var uid = attr(m, 'uid');
        if (!(uid in dist)) {
          dist[uid] = d;
          nodes[uid] = m;
          next.push(m);
          region.push(m);
        }
      }
    }
    frontier = next;
  }
  // 2 - sources: region nodes whose effective state is not ok
  var sources = region.filter(function (x) { return !isConsumer(x) && state0(x) !== 'ok'; });
  sources.sort(function (a, b) { return cmp(attr(a, 'uid'), attr(b, 'uid')); });
  // 3 - impact: label-correcting propagation to dependents, bounded by `hops` levels, within the region
  var best = {};
  var queue = sources.slice();
  for (var si = 0; si < sources.length; si++) {
    var s = sources[si];
    var sev0 = state0(s);
    best[attr(s, 'uid')] = { sev: sev0, d: 0, w: crit(s) * SEV[sev0] * W, soft: false,
                             parent: null, parent_fid: null, mode: attr(s, 'mode'), node: s };
  }
  for (var level = 1; level <= hops; level++) {
    var nextq = [];
    for (var qi = 0; qi < queue.length; qi++) {
      var b = queue[qi];
      var bb = best[attr(b, 'uid')];
      if (bb.soft || isConsumer(b)) { continue; } // bulkhead: soft-reached nodes do not propagate; consumers are leaves
      var incoming = edgesOf(b, 'incoming', DEPENDENT_TYPES).sort(byFid);
      for (var ii = 0; ii < incoming.length; ii++) {
        var e = incoming[ii];
        var a = otherEnd(e, b);
        var auid = attr(a, 'uid');
        if (!(auid in dist)) { continue; }
        var st = propagate(bb.sev, bb.mode, e.attributes.hard === true);
        if (st === null) { continue; }
        var w = crit(a) * SEV[st.sev] * Math.floor(W / (level + 1));
        var cur = best[auid];
        if (cur === undefined || w > cur.w || (w === cur.w && attr(b, 'uid') < cur.parent)) {
          best[auid] = { sev: st.sev, d: level, w: w, soft: st.soft, parent: attr(b, 'uid'), parent_fid: fidOf(e),
                         mode: attr(a, 'mode'), node: a };
          nextq.push(a);
        }
      }
    }
    var seen = {};
    queue = nextq.filter(function (x) {
      var u = attr(x, 'uid');
      if (seen[u]) { return false; }
      seen[u] = true;
      return true;
    }).sort(function (x, y) { return cmp(attr(x, 'uid'), attr(y, 'uid')); });
  }
  // 4 - owners
  var impactedUids = Object.keys(best).sort();
  var ownerEdges = [];
  var teamSet = {};
  for (var oi = 0; oi < impactedUids.length; oi++) {
    var u = impactedUids[oi];
    var owned = edgesOf(best[u].node, 'outgoing', ['OWNER_NOW']).sort(byFid);
    for (var wi = 0; wi < owned.length; wi++) {
      var team = attr(owned[wi].target, 'uid');
      ownerEdges.push([u, team, fidOf(owned[wi])]);
      teamSet[team] = true;
    }
  }
  var impacted = impactedUids.map(function (u2) {
    var x = best[u2];
    return [u2, x.sev, x.d, x.w, x.parent, x.parent_fid];
  });
  return {
    version: fed ? 2 : 1,
    region: Object.keys(dist).sort(),
    sources: sources.map(function (x) { return attr(x, 'uid'); }),
    impacted: impacted,
    teams: Object.keys(teamSet).sort(),
    owner_edges: ownerEdges,
  };
}

falkor.register('blastRadius', blastRadius);
})();
