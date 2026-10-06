#!/usr/bin/env python3
"""Showcase wrapper: the implementation is `agent_harness.trust` (AH5-06).

Imported by name, the module object is replaced, so names, private helpers and test patches resolve to the
library; loaded by file path, it carries the library's names; run as a script, it is the library CLI.
"""
import sys

from agent_harness import trust as _impl

if __name__ == "__main__":
    _impl.main()
else:
    globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})
    sys.modules[__name__] = _impl
