"""Supported retry-control interface for consumers (AH5-API-1).

A consumer registers ``RetryControl`` objects to prove that a critical ``forbid`` gate runs without internal
retries. This module is the supported import path; the type is the library's own
(``agent_harness.verification.retry.RetryControl``), not a copy.

``RetryControl`` fields (all required unless noted):

- ``control_id``: the control's identifier, as gates declare it;
- ``gate_id``, ``command``: the exact gate and command the control covers;
- ``wrapper_sha256``: the SHA-256 of the command's wrapper script;
- ``config_path``: the relative path of the build configuration the control reads; it must be a key of ``inputs``;
- ``inputs``: relative path → SHA-256 of every input file the control reads;
- ``collect_evidence(worktree, control) -> dict``: collects the evidence for one run in the candidate worktree;
- ``check_run_dir(backend_run_dir, environment) -> None``: checks the backend's run directory before launch;
- ``run_namespace`` (optional, default ``'registered-retry-control'``): the per-execution directory name.

``RetryControl.inputs_digest()`` is the SHA-256 of the canonical JSON of ``{"control_id": ..., "inputs": {...}}``
with ``inputs`` sorted by path: the value the store records as the control's registered inputs.

Callback errors: any ``Exception`` raised by ``collect_evidence`` or ``check_run_dir`` makes the gate fail with
``retry-policy-violation`` (the supervisor converts it; the exception itself is not recorded). A consumer refuses
by raising its own exception type. ``BaseException`` subclasses that are not ``Exception`` (``SystemExit``,
``KeyboardInterrupt``) are not converted.
"""
from __future__ import annotations

from .verification.retry import RetryControl

__all__ = ['RetryControl']
