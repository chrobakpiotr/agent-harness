import argparse
import hashlib
import os
import re
import sys
from importlib import resources

from agent_harness import __version__


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
    args = parser.parse_args(argv)
    if args.command != "constitution":
        return 0
    text, version, digest = constitution()
    if args.digest:
        print(f"constitution {version} {digest}")
    elif args.check is not None:  # an empty PATH fails to open (2); it is never skipped
        try:
            with open(args.check, "rb") as handle:
                size = os.fstat(handle.fileno()).st_size
                # A copy of another size has drifted; don't read an arbitrarily large file to find out.
                copy = handle.read() if size == len(text) else None
        except OSError as error:
            print(f"{args.check}: unreadable ({error.strerror})", file=sys.stderr)
            return 2
        if copy != text:
            found = f"sha256:{hashlib.sha256(copy).hexdigest()}" if copy is not None else f"{size} bytes"
            print(f"{args.check}: drifted from constitution {version} ({digest}; copy {found})", file=sys.stderr)
            return 1
        print(f"{args.check}: matches constitution {version} {digest}")
    else:
        sys.stdout.buffer.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
