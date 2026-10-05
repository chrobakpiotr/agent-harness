"""Explicit repository context and state-root resolution (AH5-03b). Read-only: never creates state.

`_git`, `_worktrees` and the control-root rule come from Showcase
`tooling/agent-harness/verification/store.py` @ 50c18f9 (see docs/migration/provenance.md).
All linked worktrees of one repository resolve to the same authority; separate repositories never do.
"""

import hashlib
import os
import pathlib
import subprocess

from agent_harness.contract import CONTRACT_VERSION, validate_repo_context


class RepositoryError(RuntimeError):
    """Stable failure code: repository-unresolved, bare-repository-unsupported, primary-worktree-ambiguous,
    base-unresolved, authority-root-symlink, authority-root-invalid."""


def _git(root, *args):
    try:
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        raise RepositoryError("repository-unresolved") from None


def _worktrees(common):
    raw = _git(common, "worktree", "list", "--porcelain", "-z")
    return [pathlib.Path(os.fsdecode(field[9:])).resolve() for field in raw.split(b"\0") if field.startswith(b"worktree ")]


def resolve_control_root(repository):
    """Primary-worktree verification control root and repository identity; never falls back to the caller."""
    try:
        caller = pathlib.Path(repository).resolve(strict=True)
    except (OSError, RuntimeError):
        raise RepositoryError("repository-unresolved") from None
    common = _git(caller, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    try:
        common = pathlib.Path(os.fsdecode(common)).resolve(strict=True)
    except (OSError, RuntimeError):
        raise RepositoryError("repository-unresolved") from None
    if _git(caller, "rev-parse", "--is-bare-repository").strip() == b"true":
        raise RepositoryError("bare-repository-unsupported")
    owners = [w for w in _worktrees(common) if (w / ".git").is_dir() and (w / ".git").resolve() == common]
    if len(owners) != 1:
        raise RepositoryError("primary-worktree-ambiguous")
    identity = hashlib.sha256(os.fsencode(common)).hexdigest()
    return owners[0] / ".agent-runs" / "control" / "verification-v2", identity


def resolve_repo_context(repository):
    """RepoContext (contract v1) for the worktree containing ``repository``. Independent of the process cwd."""
    control_root, repo_id = resolve_control_root(repository)
    caller = pathlib.Path(repository).resolve(strict=True)
    workspace = pathlib.Path(os.fsdecode(_git(caller, "rev-parse", "--show-toplevel").strip())).resolve()
    try:
        base_sha = _git(workspace, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    except RepositoryError:
        raise RepositoryError("base-unresolved") from None
    evidence_root = control_root.parents[2] / ".agent-runs"
    # A redirected state root could share or leak authority across repositories.
    for path in (evidence_root, evidence_root / "control", control_root):
        if path.is_symlink():
            raise RepositoryError("authority-root-symlink")
        if path.exists() and not path.is_dir():
            raise RepositoryError("authority-root-invalid")
    return validate_repo_context({
        "contract_version": CONTRACT_VERSION, "repo_id": repo_id, "base_sha": base_sha,
        "workspace": str(workspace), "authority_root": str(control_root), "evidence_root": str(evidence_root),
    })
