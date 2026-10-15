// GNOMON Kernel UDF library (master §8.1-§8.5), loaded as `Kernel` by gnomon/udf/loader.py.
//
// Pure functions only: no graph reads, no wall clock, no crypto module. Every word operation is
// unsigned 32-bit. Errors are thrown with a leading NAME so callers can match them; a throw aborts
// the enclosing query and nothing is written (platform fact S4).
//
// Integers reach JS as Number when |n| <= 2^53 - 1 and as BigInt beyond that (observed on the pinned
// image). INF = 2^53 - 1 is the largest accepted integer, so arithmetic here is always exact.

var LC_MAX = 1048575;

var K = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
];

var H0 = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];

function fail(name, detail) {
  throw new Error(name + ': ' + detail);
}

function describe(v) {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'bigint') return 'integer outside ±(2^53 - 1)';
  if (Array.isArray(v)) return 'list';
  return typeof v + ' ' + String(v);
}

// UTF-16 code units -> UTF-8 bytes. A lone surrogate is encoded as U+FFFD, as TextEncoder does.
function utf8(s) {
  var out = [];
  for (var i = 0; i < s.length; i++) {
    var c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
      var d = s.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) {
        c = 0x10000 + ((c - 0xd800) << 10) + (d - 0xdc00);
        i++;
      } else {
        c = 0xfffd;
      }
    } else if (c >= 0xd800 && c <= 0xdfff) {
      c = 0xfffd;
    }
    if (c < 0x80) {
      out.push(c);
    } else if (c < 0x800) {
      out.push(0xc0 | (c >>> 6), 0x80 | (c & 0x3f));
    } else if (c < 0x10000) {
      out.push(0xe0 | (c >>> 12), 0x80 | ((c >>> 6) & 0x3f), 0x80 | (c & 0x3f));
    } else {
      out.push(0xf0 | (c >>> 18), 0x80 | ((c >>> 12) & 0x3f), 0x80 | ((c >>> 6) & 0x3f), 0x80 | (c & 0x3f));
    }
  }
  return out;
}

function rotr(x, n) {
  return ((x >>> n) | (x << (32 - n))) >>> 0;
}

// FIPS 180-4 §6.2 over a byte array.
function sha256Bytes(bytes) {
  var bitLen = bytes.length * 8;
  var msg = bytes.slice();
  msg.push(0x80);
  while (msg.length % 64 !== 56) msg.push(0);
  // 64-bit big-endian bit count; the high word is exact because bitLen < 2^53.
  var hi = Math.floor(bitLen / 0x100000000);
  var lo = bitLen >>> 0;
  msg.push((hi >>> 24) & 0xff, (hi >>> 16) & 0xff, (hi >>> 8) & 0xff, hi & 0xff);
  msg.push((lo >>> 24) & 0xff, (lo >>> 16) & 0xff, (lo >>> 8) & 0xff, lo & 0xff);

  var h = H0.slice();
  var w = new Array(64);
  for (var off = 0; off < msg.length; off += 64) {
    for (var t = 0; t < 16; t++) {
      var j = off + t * 4;
      w[t] = ((msg[j] << 24) | (msg[j + 1] << 16) | (msg[j + 2] << 8) | msg[j + 3]) >>> 0;
    }
    for (t = 16; t < 64; t++) {
      var s0 = (rotr(w[t - 15], 7) ^ rotr(w[t - 15], 18) ^ (w[t - 15] >>> 3)) >>> 0;
      var s1 = (rotr(w[t - 2], 17) ^ rotr(w[t - 2], 19) ^ (w[t - 2] >>> 10)) >>> 0;
      w[t] = (w[t - 16] + s0 + w[t - 7] + s1) >>> 0;
    }
    var a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], k = h[7];
    for (t = 0; t < 64; t++) {
      var S1 = (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) >>> 0;
      var ch = ((e & f) ^ (~e & g)) >>> 0;
      var t1 = (k + S1 + ch + K[t] + w[t]) >>> 0;
      var S0 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) >>> 0;
      var maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
      var t2 = (S0 + maj) >>> 0;
      k = g;
      g = f;
      f = e;
      e = (d + t1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) >>> 0;
    }
    h[0] = (h[0] + a) >>> 0;
    h[1] = (h[1] + b) >>> 0;
    h[2] = (h[2] + c) >>> 0;
    h[3] = (h[3] + d) >>> 0;
    h[4] = (h[4] + e) >>> 0;
    h[5] = (h[5] + f) >>> 0;
    h[6] = (h[6] + g) >>> 0;
    h[7] = (h[7] + k) >>> 0;
  }
  var hex = '';
  for (var i = 0; i < 8; i++) hex += ('00000000' + h[i].toString(16)).slice(-8);
  return hex;
}

function sha256(s) {
  if (typeof s !== 'string') fail('SHA256_NOT_STRING', 'Kernel.sha256 takes a string, got ' + describe(s));
  return sha256Bytes(utf8(s));
}

// Netstring encoding of one field (§8.2): null -> "~,", int -> its decimal text, str -> "<bytes>:<str>,".
function encField(x, out) {
  if (x === null || x === undefined) {
    out.push(0x7e, 0x2c);
    return;
  }
  var text;
  if (typeof x === 'string') {
    text = x;
  } else if (typeof x === 'number' && Number.isSafeInteger(x)) {
    text = String(x);
  } else {
    fail('DIGEST_BAD_FIELD', 'Kernel.digest fields are string, integer or null, got ' + describe(x));
  }
  var bytes = utf8(text);
  var prefix = utf8(String(bytes.length) + ':');
  for (var i = 0; i < prefix.length; i++) out.push(prefix[i]);
  for (i = 0; i < bytes.length; i++) out.push(bytes[i]);
  out.push(0x2c);
}

function digest(fields) {
  if (!Array.isArray(fields)) fail('DIGEST_NOT_LIST', 'Kernel.digest takes a list, got ' + describe(fields));
  var out = [];
  for (var i = 0; i < fields.length; i++) encField(fields[i], out);
  return sha256Bytes(out);
}

// HLC components are non-negative integers no larger than INF = 2^53 - 1.
function requireInt(name, v) {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < 0) {
    fail('HLC_NOT_INT', name + ' must be an integer in [0, 2^53 - 1], got ' + describe(v));
  }
}

function hlc(l, c) {
  if (c > LC_MAX) fail('LC_OVERFLOW', 'logical counter ' + c + ' exceeds LC_MAX ' + LC_MAX + ' at pt ' + l);
  return { pt: l, lc: c };
}

// Replay and sim return the recorded clock, which must be strictly after the current one (§2.2).
function recorded(pt, lc, rec_pt, rec_lc) {
  requireInt('rec_pt', rec_pt);
  requireInt('rec_lc', rec_lc);
  if (!(rec_pt > pt || (rec_pt === pt && rec_lc > lc))) {
    fail('HLC_REGRESSION', 'recorded (' + rec_pt + ', ' + rec_lc + ') is not after (' + pt + ', ' + lc + ')');
  }
  return hlc(rec_pt, rec_lc);
}

function badMode(mode) {
  fail('BAD_MODE', "mode must be 'live', 'replay' or 'sim', got " + describe(mode));
}

function tick(pt, lc, wall, mode, rec_pt, rec_lc) {
  requireInt('pt', pt);
  requireInt('lc', lc);
  if (mode === 'live') {
    requireInt('wall', wall);
    var l = Math.max(pt, wall);
    return hlc(l, l === pt ? lc + 1 : 0);
  }
  if (mode === 'replay' || mode === 'sim') return recorded(pt, lc, rec_pt, rec_lc);
  return badMode(mode);
}

function recv(pt, lc, wall, m_pt, m_lc, mode, rec_pt, rec_lc) {
  if (mode !== 'live') return tick(pt, lc, wall, mode, rec_pt, rec_lc);
  requireInt('pt', pt);
  requireInt('lc', lc);
  requireInt('wall', wall);
  requireInt('m_pt', m_pt);
  requireInt('m_lc', m_lc);
  var l = Math.max(pt, m_pt, wall);
  var c;
  if (l === pt && l === m_pt) c = Math.max(lc, m_lc) + 1;
  else if (l === pt) c = lc + 1;
  else if (l === m_pt) c = m_lc + 1;
  else c = 0;
  return hlc(l, c);
}

function requireChainArg(name, v, kind) {
  var ok = kind === 'string'
    ? typeof v === 'string'
    : typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
  if (!ok) fail('CHAIN_BAD_ARG', 'Kernel.chain ' + name + ' must be a ' + kind + ', got ' + describe(v));
}

// The hash of a rewrite, linking it to its parent (§8.5). "rw/1" versions the chain format.
function chain(prev, id, seq, pt, lc, rule_sha, agent, params, read_seq, vk_digest) {
  requireChainArg('prev', prev, 'string');
  requireChainArg('id', id, 'string');
  requireChainArg('seq', seq, 'integer');
  requireChainArg('pt', pt, 'integer');
  requireChainArg('lc', lc, 'integer');
  requireChainArg('rule_sha', rule_sha, 'string');
  requireChainArg('agent', agent, 'string');
  requireChainArg('params', params, 'string');
  requireChainArg('read_seq', read_seq, 'integer');
  requireChainArg('vk_digest', vk_digest, 'string');
  return digest(['rw/1', prev, id, seq, pt, lc, rule_sha, agent, params, read_seq, vk_digest]);
}

falkor.register('sha256', sha256);
falkor.register('digest', digest);
falkor.register('tick', tick);
falkor.register('recv', recv);
falkor.register('chain', chain);
