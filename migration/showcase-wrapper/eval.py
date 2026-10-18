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


def repo_root():
    return _impl.repo_root(REPO)


def load_suite(value):
    return _impl.load_suite(value, DEFAULT_SUITES)


def run_suite(path, doc, args):
    return _impl.run_suite(path, doc, args, anchor=REPO)


def main(argv=None):
    _impl.main(argv, suites_dir=DEFAULT_SUITES, anchor=REPO)


if __name__ == '__main__':
    main()
