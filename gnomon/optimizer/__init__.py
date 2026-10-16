"""The self-optimizing engine (master §16): telemetry, plan analysis, shadow trials, the integer cost model and the
decided IndexChange. It never runs DDL on live; the reconciler (gnomon/ops/reconciler.py) does that after M1."""
