// BODY - PeerTenants@1 (S2, sys graph): two active tenants agree to exchange the listed message kinds. a < b, so a
// pair has exactly one peering key.
MATCH (a:Tenant {id: $p.a}), (b:Tenant {id: $p.b})
WHERE all(t IN [a, b] WHERE t.state = 'active') AND $p.a < $p.b
OPTIONAL MATCH (ex:Peering {key: $p.a + '|' + $p.b})
WITH KCTX, a, b, count(ex) AS n
WHERE n = 0 AND size($p.kinds) >= 1
CREATE (:Peering {key: $p.a + '|' + $p.b, a: $p.a, b: $p.b, kinds: $p.kinds, state: 'active',
        registered_seq: nseq, rw: $pid})
WITH KCTX
