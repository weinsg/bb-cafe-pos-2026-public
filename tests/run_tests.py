"""Test runner script for Beans&Beats Queue Management System.

Runs all core unit and integration test suites using Python's standard unittest runner.
Usage:
    python tests/run_tests.py
    python -m tests.run_tests
"""

import os
import sys
import time

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest


def run_all_tests():
    start_time = time.time()
    print("=" * 70)
    print("  Beans&Beats x Christmas Together 2026 — Core Test Suite")
    print("=" * 70)

    # Discover and load all test cases in the 'tests' package
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.dirname(__file__), pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    duration = time.time() - start_time
    print("-" * 70)
    print(f"Ran {result.testsRun} tests in {duration:.2f}s")

    # Clean up test database if present
    test_db = "test_beansbeats.db"
    if os.path.exists(test_db):
        try:
            os.remove(test_db)
        except Exception:
            pass

    if result.wasSuccessful():
        print("\n[SUCCESS] All backend tests passed. Browser and deployment checks are separate.")
        print("=" * 70)
        sys.exit(0)
    else:
        print(
            f"\n[FAILURE] {len(result.failures)} failed, {len(result.errors)} errors."
        )
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
