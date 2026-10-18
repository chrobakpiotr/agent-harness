#!/usr/bin/env python3
"""Showcase wrapper over `agent_harness.eval_runner` (AH5-10).

Binds Showcase's suites (`evals/`, which stay Showcase-owned) and its repository anchor (this file's checkout),
which the library takes as explicit parameters. Run as a script, it is the eval CLI.
"""
import pathlib

from agent_harness import eval_runner as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})
HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_SUITES = HERE / 'evals'


def _anchor():
    # The source tried this file's checkout, then the working directory's; the library takes one explicit anchor.
    import subprocess
    found = subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--show-toplevel'], capture_output=True, check=False)
    return REPO if found.returncode == 0 else pathlib.Path.cwd()


def repo_root():
    return _impl.repo_root(_anchor())


def load_suite(value):
    return _impl.load_suite(value, DEFAULT_SUITES)


def run_suite(path, doc, args):
    return _impl.run_suite(path, doc, args, anchor=_anchor())


def main(argv=None):
    _impl.main(argv, suites_dir=DEFAULT_SUITES, anchor=_anchor())


if __name__ == '__main__':
    main()
