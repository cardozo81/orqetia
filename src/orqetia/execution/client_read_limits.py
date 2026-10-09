"""Shared read amplification bound for client execution projections.

This cap does not change internal retries, provider execution or worker replay.
"""

MAX_CLIENT_ATTEMPT_ROWS = 5_000
