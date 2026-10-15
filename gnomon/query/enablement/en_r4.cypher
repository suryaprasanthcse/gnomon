// EN-R4 - OpenIncident: untriggered alerts without an open incident for the same (service, signature)
//         view: Alert:{alert}:state, Incident:{service}:{signature}, phantom Incident:*
// The open-duplicate filter (D-31) keeps the query sound against guard G8. The duplicate count sits in a
// sum(CASE) over an unfiltered OPTIONAL MATCH (FACTS S25); aggregates stay last in each WITH (S23).
MATCH (al:Alert)-[:ON]->(s:Service)
OPTIONAL MATCH (al)-[tr:TRIGGERED]->()
WITH al, s, count(tr) AS n_tr
WHERE n_tr = 0
OPTIONAL MATCH (op:Incident {state: 'open'})
WITH al, s, sum(CASE WHEN op.service = s.id AND op.signature = al.signature THEN 1 ELSE 0 END) AS n_dup
WHERE n_dup = 0
MATCH (h:Head)
RETURN h.seq AS read_seq, al.id AS alert, s.id AS service, al.signature AS signature, al.severity AS severity
ORDER BY alert
