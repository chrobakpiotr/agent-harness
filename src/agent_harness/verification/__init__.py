"""Shared, read-only verification planning primitives.

Planning is advisory. The execution layer must perform repository admission under
its lock before calling evaluate_ready_gate; this package grants no execution authority.
"""
