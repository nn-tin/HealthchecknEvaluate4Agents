from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval_tool.bootstrap import build_runner, load_cases  # noqa: E402
from eval_tool.config import load_config  # noqa: E402
from eval_tool.runner import EvaluationRunner  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TicketAgents healthcheck and multi-turn evaluation MVP"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    scheduler = sub.add_parser("scheduler", help="Run scheduled healthchecks")
    scheduler.add_argument("--once", action="store_true", help="Check due schedules once, then exit")
    evaluation_scheduler = sub.add_parser("evaluation-scheduler", help="Run scheduled evaluation cases")
    evaluation_scheduler.add_argument("--once", action="store_true", help="Check due schedules once, then exit")
    sub.add_parser("db-init", help="Create/update PostgreSQL schema")
    backfill = sub.add_parser("backfill", help="Import local JSON results into PostgreSQL")
    backfill.add_argument("--runs-dir", default=str(ROOT / "data" / "runs"))

    health = sub.add_parser("healthcheck", help="Run healthcheck probes")
    health.add_argument(
        "--agent",
        default="all",
        choices=["all", "leave", "late", "helpdesk", "bookroom"],
    )

    evaluate = sub.add_parser("eval", help="Run multi-turn evaluation cases")
    evaluate.add_argument("--cases", required=True, help="YAML/JSON case file")
    evaluate.add_argument(
        "--agent",
        choices=["leave", "late", "helpdesk", "bookroom"],
        help="Optionally filter cases by agent",
    )
    evaluate.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run selected case ID; repeat for multiple cases",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args()
    config = load_config(ROOT)
    if args.command in {"db-init", "backfill", "scheduler", "evaluation-scheduler"}:
        try:
            from eval_tool.db import migrate
            if args.command == "scheduler":
                from eval_tool.scheduler import HealthcheckScheduler
                HealthcheckScheduler(config).serve(once=args.once)
                return 0
            if args.command == "evaluation-scheduler":
                from eval_tool.scheduler import EvaluationScheduler
                EvaluationScheduler(config).serve(once=args.once)
                return 0
            migrate(config.database_url, ROOT)
            if args.command == "backfill":
                from eval_tool.backfill import backfill
                saved, failed = backfill(config, Path(args.runs_dir))
                print(f"Imported={saved} failed={failed}")
                return 1 if failed else 0
            print("Database schema ready")
            return 0
        except Exception as exc:
            print(f"{type(exc).__name__}: operation failed; check database and schedule configuration", file=sys.stderr)
            return 1
    if args.command == "healthcheck":
        runner = EvaluationRunner(config)
        agent_ids = list(config.agents) if args.agent == "all" else [args.agent]
        exit_code = 0
        for agent_id in agent_ids:
            try:
                result = runner.healthcheck(agent_id)
                print(
                    json.dumps(
                        result.model_dump(mode="json"),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                if result.status == "Error":
                    exit_code = 1
            except Exception as exc:
                exit_code = 1
                print(f"{agent_id}: {exc}", file=sys.stderr)
        return exit_code

    cases = load_cases(args.cases)
    if args.agent:
        cases = [case for case in cases if case.agent == args.agent]
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case.id in selected]
    if not cases:
        print("No matching cases", file=sys.stderr)
        return 2

    runner = build_runner(config)
    results = runner.run_cases(cases)
    for result in results:
        print(
            f"{result.case_id}: {result.evaluation.verdict} "
            f"score={result.evaluation.average_score} file={result.result_file}"
        )
    return 1 if any(r.evaluation.verdict != "PASS" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
