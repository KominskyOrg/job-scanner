#!/usr/bin/env python3
"""
Regression harness for job evaluation pipeline.
Runs evaluator + post_evaluator across test JDs and compares to expected outcomes.

Usage:
    python3 src/run_regression.py           # Run all tests
    python3 src/run_regression.py --dry-run # Show what would be tested
    python3 src/run_regression.py --verbose # Show detailed output
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from evaluator import JobEvaluator
from post_evaluator import validate_apply_gates, load_staffing_config


def load_test_jds(test_dir: Path) -> dict[str, str]:
    """Load all test JD files from the test directory."""
    jds = {}
    for jd_file in test_dir.glob("*.txt"):
        name = jd_file.stem
        with open(jd_file) as f:
            jds[name] = f.read()
    return jds


def load_expected_outcomes(test_dir: Path) -> dict:
    """Load expected outcomes from JSON file."""
    expected_path = test_dir / "expected_outcomes.json"
    with open(expected_path) as f:
        return json.load(f)


def extract_title_from_jd(jd_text: str) -> str:
    """Extract job title from first line of JD."""
    lines = jd_text.strip().split('\n')
    return lines[0].strip() if lines else "Unknown"


def extract_company_from_jd(jd_text: str) -> str:
    """Extract company name from second line of JD (format: Company - Location)."""
    lines = jd_text.strip().split('\n')
    if len(lines) > 1:
        company_line = lines[1].strip()
        if ' - ' in company_line:
            return company_line.split(' - ')[0].strip()
        return company_line
    return "Unknown"


def run_single_test(
    evaluator: JobEvaluator,
    staffing_config: dict,
    name: str,
    jd_text: str,
    expected: dict,
    verbose: bool = False
) -> tuple[bool, str, dict]:
    """
    Run a single test case.

    Returns:
        (passed, reason, details)
    """
    title = extract_title_from_jd(jd_text)
    company = extract_company_from_jd(jd_text)

    try:
        # Run evaluator
        evaluation, eval_metadata = evaluator.evaluate_job(jd_text)

        # Run post gates
        evaluation, gate_results = validate_apply_gates(
            evaluation,
            title,
            jd_text,
            company,
            staffing_config
        )

        final_decision = evaluation.final_decision
        expected_decision = expected.get("final_decision")
        alt_decision = expected.get("alt")

        # Check if passed
        passed = (final_decision == expected_decision) or (alt_decision and final_decision == alt_decision)

        reason = ""
        if passed:
            reason = f"Got {final_decision} (expected {expected_decision})"
            if alt_decision and final_decision == alt_decision:
                reason = f"Got {final_decision} (alt acceptable)"
        else:
            reason = f"Got {final_decision}, expected {expected_decision}"
            if alt_decision:
                reason += f" or {alt_decision}"

        details = {
            "final_decision": final_decision,
            "expected_decision": expected_decision,
            "alt_decision": alt_decision,
            "role_fit_score": evaluation.role_fit_score,
            "confidence_signal": evaluation.confidence_signal,
            "role_classification": evaluation.role_classification,
            "remote_status": evaluation.remote_status,
            "gate_results": gate_results,
            "eval_metadata": eval_metadata if verbose else None,
        }

        return passed, reason, details

    except Exception as e:
        return False, f"Error: {e}", {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Run regression tests for job evaluator")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be tested")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--test", type=str, help="Run only a specific test by name")
    args = parser.parse_args()

    # Paths
    base_dir = Path(__file__).parent.parent
    test_dir = base_dir / "test_jds"
    config_dir = base_dir / "config"

    # Load test data
    test_jds = load_test_jds(test_dir)
    expected_outcomes = load_expected_outcomes(test_dir)

    print(f"Found {len(test_jds)} test JDs")
    print(f"Found {len(expected_outcomes)} expected outcomes")
    print()

    if args.dry_run:
        print("DRY RUN - Would test:")
        for name in sorted(test_jds.keys()):
            if name in expected_outcomes:
                exp = expected_outcomes[name]
                print(f"  {name}: expect {exp['final_decision']}")
                if exp.get("alt"):
                    print(f"    (or {exp['alt']})")
            else:
                print(f"  {name}: NO EXPECTED OUTCOME")
        return

    # Initialize evaluator
    print("Initializing evaluator...")
    evaluator = JobEvaluator(
        prompt_path=config_dir / "prompt.txt",
        profile_path=config_dir / "profile_screening.txt"
    )
    staffing_config = load_staffing_config()
    print(f"  Prompt version: {evaluator.prompt_version}")
    print(f"  Profile version: {evaluator.profile_version}")
    print()

    # Filter tests if specific test requested
    if args.test:
        if args.test not in test_jds:
            print(f"ERROR: Test '{args.test}' not found")
            print(f"Available: {', '.join(sorted(test_jds.keys()))}")
            sys.exit(1)
        test_jds = {args.test: test_jds[args.test]}

    # Run tests
    results = []
    failures = []

    print("Running tests...")
    print("-" * 60)

    for name in sorted(test_jds.keys()):
        if name not in expected_outcomes:
            print(f"SKIP {name}: no expected outcome defined")
            continue

        jd_text = test_jds[name]
        expected = expected_outcomes[name]

        passed, reason, details = run_single_test(
            evaluator, staffing_config, name, jd_text, expected, args.verbose
        )

        status = "PASS" if passed else "FAIL"
        icon = "✓" if passed else "✗"
        print(f"{icon} {status} {name}: {reason}")

        if args.verbose and details:
            print(f"    Score: {details.get('role_fit_score')}, "
                  f"Classification: {details.get('role_classification')}")
            if details.get("gate_results"):
                reasons = details["gate_results"].get("post_gates_reasons", [])
                if reasons:
                    print(f"    Gate reasons: {', '.join(reasons[:3])}")

        results.append({
            "name": name,
            "passed": passed,
            "reason": reason,
            "details": details,
        })

        if not passed:
            failures.append(name)

    print("-" * 60)

    # Summary
    passed_count = len(results) - len(failures)
    total_count = len(results)

    print(f"\nResults: {passed_count}/{total_count} passed")

    if failures:
        print("\nFAILED TESTS:")
        for name in failures:
            result = next(r for r in results if r["name"] == name)
            print(f"  {name}: {result['reason']}")
            if args.verbose:
                notes = expected_outcomes.get(name, {}).get("notes", "")
                if notes:
                    print(f"    Notes: {notes}")
        sys.exit(1)
    else:
        print("\nAll tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
