// RD-entity - One entity's properties      view: {Label}:{id}:state
MATCH (h:Head)
MATCH (e:Entity {uid: $uid})
RETURN h.seq AS read_seq, labels(e) AS labels, properties(e) AS props
