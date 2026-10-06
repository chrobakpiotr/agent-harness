import argparse
import errno
import hashlib
import json
import os
import re
import stat
import sys
from importlib import resources
from pathlib import Path

from agent_harness import __version__, contract


def constitution():
    """The canonical shared agent contract shipped with this version: (text bytes, version, digest)."""
    text = (resources.files("agent_harness") / "constitution.md").read_bytes()
    version = re.search(rb"^- Version: (\S+)$", text, re.MULTILINE).group(1).decode()
    return text, version, "sha256:" + hashlib.sha256(text).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="agent-harness",
        description="Agent execution harness. The library API is agent_harness.contract and agent_harness.execution; "
                    "no models are contacted.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    shared = commands.add_parser("constitution", help="print or check the shared agent contract (constitution)")
    mode = shared.add_mutually_exclusive_group()
    mode.add_argument("--digest", action="store_true", help="print its version and sha256")
    mode.add_argument("--check", metavar="PATH",
                      help="exit 0 if PATH is an exact copy, 1 if it drifted, 2 if unreadable or misused")
    qualification = commands.add_parser("qualification", help="check a target qualification report")
    qualification.add_argument("--check", metavar="FILE", required=True, help="the qualification report (JSON)")
    qualification.add_argument("--evidence-root", metavar="DIR", help="verify every evidence file under DIR")
    qualification.add_argument("--capability-report", metavar="FILE",
                               help="also check that this capability report binds to the qualification")
    args = parser.parse_args(argv)
    if args.command == "qualification":
        return _qualification(args)
    if args.command != "constitution":
        return 0
    text, version, digest = constitution()
    if args.digest:
        print(f"constitution {version} {digest}")
    elif args.check is not None:  # an empty PATH is unreadable (2); it is never skipped
        try:
            # Only a regular file is a copy: a FIFO or device could block or never end. Opening non-blocking
            # and checking the open handle leaves no gap between the check and the read.
            fd = os.open(args.check, os.O_RDONLY | os.O_NONBLOCK)
            with open(fd, "rb") as handle:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError(errno.EINVAL, "not a regular file")
                copy = handle.read(len(text) + 1)  # bounded: anything longer has drifted
        except OSError as error:
            print(f"{args.check}: unreadable ({error.strerror})", file=sys.stderr)
            return 2
        if copy != text:
            found = f"sha256:{hashlib.sha256(copy).hexdigest()}" if len(copy) <= len(text) else "longer"
            print(f"{args.check}: drifted from constitution {version} ({digest}; copy {found})", file=sys.stderr)
            return 1
        print(f"{args.check}: matches constitution {version} {digest}")
    else:
        sys.stdout.buffer.write(text)
    return 0


def _qualification(args):
    """Exit 0: valid and passing; 1: valid but not passing or not bindable; 2: invalid or unreadable."""
    try:
        doc = contract.validate_qualification_report(json.loads(Path(args.check).read_text(encoding="utf-8")))
        if args.evidence_root is not None:
            contract.verify_qualification_evidence(doc, args.evidence_root)
        report = None
        if args.capability_report is not None:
            report = json.loads(Path(args.capability_report).read_text(encoding="utf-8"))
            contract.validate_capability_report(report)
    except (OSError, ValueError) as error:  # ContractError and JSON errors are ValueErrors
        print(f"{args.check}: invalid ({error})", file=sys.stderr)
        return 2
    passes = contract.qualification_passes(doc)
    print(f"{args.check}: qualification {doc['target']} {contract.qualification_digest(doc)} "
          f"{'passes' if passes else 'does not pass'}")
    if report is not None:
        try:
            contract.validate_capability_binding(report, doc)
        except contract.ContractError as error:
            print(f"{args.capability_report}: does not bind ({error})", file=sys.stderr)
            return 1
    return 0 if passes else 1


if __name__ == "__main__":
    raise SystemExit(main())
