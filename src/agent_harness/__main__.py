import argparse

from agent_harness import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="agent-harness",
        description="Agent execution harness. Bootstrap build: no commands yet; no models are contacted.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
