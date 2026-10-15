// why(pid) - the rewrite with its causal chains (lists of seqs, newest first); same shape as WHY-entity (master 21.3)
MATCH (w:Rewrite {id: $pid})
OPTIONAL MATCH chain = (w)-[:CAUSED_BY*1..8]->(:Rewrite)
WITH w, collect([n IN nodes(chain) | n.seq]) AS causes
RETURN w.id AS pid, w.seq AS seq, w.rule AS rule, w.agent AS agent, causes
