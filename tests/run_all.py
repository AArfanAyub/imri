"""Run the whole IMRI test suite with one command.

    python tests/run_all.py            # everything
    python tests/run_all.py --fast     # skip the HTTP suite (slowest)

Standard library only, like the product. Exit code 0 means every test passed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

#: Modules in the order they are cheapest to diagnose first.
MODULES = [
    "test_db",
    "test_security",
    "test_catalog",
    "test_circulation",
    "test_transfer",
    "test_api",
]

#: The HTTP suite boots a real socket per test, so it dominates the runtime.
SLOW = {"test_api"}


def build_suite(names: list[str]) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in names:
        suite.addTests(loader.loadTestsFromName(name))
    return suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the IMRI test suite.")
    parser.add_argument("--fast", action="store_true", help="skip the HTTP suite")
    parser.add_argument("--only", nargs="*", help="run only these modules")
    parser.add_argument("--quiet", action="store_true", help="summary only")
    args = parser.parse_args(argv)

    names = args.only or [name for name in MODULES if not (args.fast and name in SLOW)]
    unknown = [name for name in names if name not in MODULES]
    if unknown:
        print("unknown module(s): %s" % ", ".join(unknown))
        return 2

    print("IMRI test suite — %d module(s)" % len(names))
    print("=" * 60)

    total_run = total_fail = total_error = total_skip = 0
    started = time.perf_counter()
    failures: list[str] = []

    for name in names:
        module_started = time.perf_counter()
        suite = build_suite([name])
        runner = unittest.TextTestRunner(verbosity=1 if args.quiet else 2, buffer=False)
        result = runner.run(suite)
        elapsed = time.perf_counter() - module_started

        total_run += result.testsRun
        total_fail += len(result.failures)
        total_error += len(result.errors)
        total_skip += len(result.skipped)
        for case, _ in result.failures + result.errors:
            failures.append("%s: %s" % (name, case))
        print("  %-18s %3d tests  %5.1fs  %s" % (
            name,
            result.testsRun,
            elapsed,
            "ok" if result.wasSuccessful() else "FAILED",
        ))

    elapsed = time.perf_counter() - started
    print("=" * 60)
    print("  %d tests in %.1fs — %d failed, %d errors, %d skipped" % (
        total_run, elapsed, total_fail, total_error, total_skip,
    ))
    if failures:
        print("\nFailures:")
        for line in failures:
            print("  - %s" % line)
    print("\n%s" % ("PASS" if not failures else "FAIL"))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
