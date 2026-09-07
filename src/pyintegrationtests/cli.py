"""Thin CLI; pytest remains the only test runner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from . import __version__
from .cleanup import cleanup
from .config import load_config
from .context import Context
from .errors import IntegrationError
from .journal import Journal
from .pytest_plugin import load_suite
from .registry import default_registry, validate_resource
from .schema import suite_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pyintegrationtests")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "list", "run"):
        p = sub.add_parser(command)
        p.add_argument("paths", nargs="+", type=Path)
        p.add_argument("--config")
        p.add_argument("--env-file")
        if command == "run":
            p.add_argument("--live", action="store_true")
            p.add_argument("--json-report")
            p.add_argument("--junit-xml")
            p.add_argument("--case-seconds", type=float)
            p.add_argument("-k")
            p.add_argument("--label", action="append", default=[])
    p = sub.add_parser("cleanup")
    p.add_argument("journal", type=Path)
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "cleanup":
            settings, environment = load_config(args.config, args.env_file)
            registry = default_registry(settings)
            journal = Journal.load(args.journal, settings)
            for entry in journal.data.entries:
                validate_resource(entry.resource, registry, settings)
            context = Context(
                settings,
                environment,
                registry,
                journal.data.suite_id,
                journal.data.case_id,
                args.config.parent,
                {},
                run_id=journal.data.run_id,
            )
            try:
                errors = cleanup(context, journal)
            finally:
                context.close()
            for error in errors:
                print(error, file=sys.stderr)
            return 1 if errors else 0
        if args.command == "run":
            options = [str(path) for path in args.paths]
            for name, flag in (
                ("config", "--pit-config"),
                ("env_file", "--pit-env-file"),
                ("json_report", "--pit-json"),
                ("junit_xml", "--junitxml"),
                ("case_seconds", "--pit-case-seconds"),
                ("k", "-k"),
            ):
                if getattr(args, name) is not None:
                    options.extend([flag, str(getattr(args, name))])
            if args.live:
                options.append("--pit-live")
            for label in args.label:
                options.extend(["--pit-label", label])
            return int(pytest.main(options))
        count = 0
        for path in suite_files(args.paths):
            _, suite, _, _, _ = load_suite(path, args.config, args.env_file)
            for case in suite.tests:
                count += 1
                if args.command == "list":
                    print(f"{path}::{case.id}")
        if args.command == "validate":
            print(f"Validated {count} cases (offline)")
        return 0 if count else 5
    except IntegrationError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
