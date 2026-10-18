"""Showcase wrapper: the implementation is `agent_harness.verification.workspace` (AH5-08a).

Imported by name, the module object is replaced, so names, private helpers and test patches resolve to the
library; loaded by file path, it carries the library's names.
"""
import sys

from agent_harness.verification import workspace as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})
sys.modules[__name__] = _impl
