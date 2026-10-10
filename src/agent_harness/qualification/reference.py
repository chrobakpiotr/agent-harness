"""Reviewed reference qualifications shipped with the library (ADR 0002 amendment, review per tuple).

``reviewed_reference(target)`` returns ``(report, evidence_root)`` for ``qualify(..., reviewed=...)`` and
``QualifiedDockerBackend(..., reviewed=...)``: the one independently reviewed run of that target with this library's
probe code and policy. Kept apart from ``__init__.py`` so adding a reference does not change ``probe_digest``.
"""

import json
from importlib import resources
from pathlib import Path

from .. import contract


def reviewed_reference(target="showcase-docker-desktop-linux-guest"):
    root = Path(str(resources.files(__package__).joinpath("reviewed", target)))
    if not (root / "report.json").is_file():
        raise contract.ContractError("NOT_QUALIFIED", f"no reviewed reference for {target}")
    report = contract.validate_qualification_report(json.loads((root / "report.json").read_text()))
    if not contract.qualification_passes(report, root / "evidence"):
        raise contract.ContractError("NOT_QUALIFIED", f"the shipped reference for {target} does not pass")
    return report, root / "evidence"
