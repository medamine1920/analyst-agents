"""Connectors to the tools analysts already use: bring data in (as a data source) or send results out.

Every connector goes through the same safe HTTP layer (public addresses only, timeouts, retries,
size limits) and keeps its credentials encrypted. Kinds are listed in kinds.py.
"""
