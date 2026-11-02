"""Command-line evaluation harness for fixture replay and live scored runs."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from .cases import EvalCase, load_case, validate_case
from .http_client import IncidentApiClient
from .injector import FlagdFaultInjector
from .manifest import load_manifest
from .runner import EvaluationRunner, load_run_record
from .signals import PollPolicy, PrometheusClient, SignalPoller


def _is_heldout(path: Path) -> bool:
    return any(
        part.casefold().replace("-", "").replace("_", "") == "heldout" for part in path.parts
    )


def _load_cases(cases_dir: Path, manifest_path: Path) -> tuple[list[EvalCase], dict[str, Any]]:
    faults = load_manifest(manifest_path)
    paths = sorted(cases_dir.glob("*.json"))
    if not paths:
        raise ValueError(f"no case files found in {cases_dir}")
    cases = []
    problems = []
    for path in paths:
        case = load_case(path)
        if issues := validate_case(case, faults):
            problems.extend(f"{path.name}: {issue}" for issue in issues)
        else:
            cases.append(case)
    if problems:
        raise ValueError("invalid evaluation cases:\n" + "\n".join(problems))
    return cases, faults


def _record_passed(case: EvalCase, record: dict) -> bool:
    return bool(
        record["correct_root_cause"]
        and record["correct_action_proposed"]
        and record["verified_recovery"]
        and record["harmful_actions"] == case["expected"]["harmful_actions"]
        and record["tool_calls"] <= case["expected"]["max_tool_calls"]
        and not record.get("error")
    )


def run_mock(args: argparse.Namespace) -> int:
    try:
        cases, _faults = _load_cases(args.cases, args.manifest)
    except ValueError as exc:
        print(f"mock FAIL: {exc}")
        return 1

    passed = 0
    contract_failures = 0
    grouped: dict[str, list[bool]] = defaultdict(list)
    print(f"mock mode: replaying {len(cases)} case(s); no model or live fault injection")
    for case in cases:
        fixture = args.fixtures / f"{case['case_id']}.runrecord.json"
        if not fixture.is_file():
            print(f"  {case['case_id']}: FAIL — no fixture at {fixture}")
            contract_failures += 1
            grouped[case["complexity"]].append(False)
            continue
        try:
            record = load_run_record(fixture)
        except (OSError, ValueError) as exc:
            print(f"  {case['case_id']}: FAIL — invalid fixture: {exc}")
            contract_failures += 1
            grouped[case["complexity"]].append(False)
            continue
        mismatches = []
        for field in ("case_id", "fault_id", "complexity"):
            if record[field] != case[field]:
                mismatches.append(f"{field} disagrees with case")
        if record["config_id"] != args.config:
            mismatches.append(f"config_id is {record['config_id']!r}, expected {args.config!r}")
        if mismatches:
            print(f"  {case['case_id']}: FAIL — {'; '.join(mismatches)}")
            contract_failures += 1
            grouped[case["complexity"]].append(False)
            continue
        ok = _record_passed(case, record)
        passed += int(ok)
        grouped[case["complexity"]].append(ok)
        print(f"  {case['case_id']}: {'PASS' if ok else 'FAIL'}")

    _print_groups(grouped, args.group_by)
    rate = passed / len(cases)
    print(f"pass rate: {passed}/{len(cases)} = {rate:.3f}; required {args.fail_under:.3f}")
    if contract_failures:
        print(f"contract failures: {contract_failures} (always fatal)")
    return 0 if not contract_failures and rate >= args.fail_under else 1


def run_live(args: argparse.Namespace) -> int:
    if not args.yes_inject_faults:
        print("live mode refused: pass --yes-inject-faults to acknowledge lab mutation")
        return 2
    try:
        cases, faults = _load_cases(args.cases, args.manifest)
    except ValueError as exc:
        print(f"live FAIL: {exc}")
        return 1

    injector = FlagdFaultInjector(args.flagd_config)
    poller = SignalPoller(
        PrometheusClient(args.prometheus_url),
        PollPolicy(
            timeout_seconds=args.signal_timeout,
            interval_seconds=args.signal_interval,
            consecutive_successes=args.signal_successes,
        ),
    )
    runner = EvaluationRunner(
        injector=injector,
        poller=poller,
        system=IncidentApiClient(
            args.api_url,
            run_timeout_seconds=args.run_timeout,
            poll_interval_seconds=args.api_poll_interval,
        ),
        artifacts_dir=args.artifacts,
    )

    passed = 0
    grouped: dict[str, list[bool]] = defaultdict(list)
    print(f"live mode: running {len(cases)} case(s) against {args.config}")
    for case in cases:
        result = runner.run(case, faults[case["fault_id"]], args.config)
        passed += int(result.score.passed)
        grouped[case["complexity"]].append(result.score.passed)
        suffix = "" if result.score.passed else f" — {'; '.join(result.score.reasons)}"
        print(f"  {case['case_id']}: {'PASS' if result.score.passed else 'FAIL'}{suffix}")
        print(f"    record: {result.artifact_path}")

    _print_groups(grouped, args.group_by)
    rate = passed / len(cases)
    print(f"pass rate: {passed}/{len(cases)} = {rate:.3f}; required {args.fail_under:.3f}")
    return 0 if rate >= args.fail_under else 1


def _print_groups(grouped: dict[str, list[bool]], group_by: str | None) -> None:
    if group_by != "complexity":
        return
    print("by complexity:")
    for name, values in sorted(grouped.items()):
        print(f"  {name}: {sum(values)}/{len(values)}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run On-Call Copilot evaluations.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="replay committed development fixtures")
    mode.add_argument("--live", action="store_true", help="inject faults and call the incident API")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("eval/faults/manifest.yaml"))
    parser.add_argument("--fixtures", type=Path, default=Path("eval/fixtures"))
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/runs"))
    parser.add_argument("--fail-under", type=float, required=True)
    parser.add_argument(
        "--config", choices=("c0_workflow", "c1_single", "c2_team"), default="c1_single"
    )
    parser.add_argument("--group-by", choices=("complexity",), default=None)
    parser.add_argument("--allow-heldout", action="store_true")
    parser.add_argument("--yes-inject-faults", action="store_true")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--prometheus-url", default="http://10.241.2.69:9090")
    parser.add_argument("--flagd-config", type=Path, default=Path("deploy/flagd/demo.flagd.json"))
    parser.add_argument("--run-timeout", type=float, default=600.0)
    parser.add_argument("--api-poll-interval", type=float, default=1.0)
    parser.add_argument("--signal-timeout", type=float, default=120.0)
    parser.add_argument("--signal-interval", type=float, default=5.0)
    parser.add_argument("--signal-successes", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not 0 <= args.fail_under <= 1:
        parser.error("--fail-under must be between 0 and 1")
    if _is_heldout(args.cases) and not args.allow_heldout:
        parser.error("held-out cases require the explicit --allow-heldout acknowledgement")
    return run_mock(args) if args.mock else run_live(args)


if __name__ == "__main__":
    sys.exit(main())
