// BODY - RegisterTenant@1 (S1, sys graph): a tenant joins the federation registry, once.
OPTIONAL MATCH (ex:Tenant {id: $p.id})
WITH KCTX, count(ex) AS n
WHERE n = 0 AND $p.id <> '_sys'
CREATE (:Tenant {id: $p.id, kernel_sha: $p.kernel_sha, state: 'active', registered_seq: nseq, rw: $pid})
WITH KCTX
