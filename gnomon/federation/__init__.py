"""Multi-tenant federation (master §17, T-D): the registry graph, channels, the relay and the federated log.

Tenants never share a graph. Every cross-tenant effect is a message that the receiver applies by a rewrite in its own
log (P2 receive, P3 ack); the relay only proposes those rewrites and keeps no durable state of its own.
"""
