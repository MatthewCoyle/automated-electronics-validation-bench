"""PowerShell-friendly simulation CLI with distinct failure and error exit codes."""
import argparse
import logging
from pathlib import Path
from .model import load_plan
from .runner import run
from .report import write_reports


def main(argv=None):
    parser = argparse.ArgumentParser(description="Electronics validation simulation; no hardware")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/latest"))
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(args.output_dir / "execution.log", mode="w", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger = logging.getLogger("validationbench")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            results = run(plan)
            counts = write_reports(plan, results, args.output_dir)
        finally:
            logger.removeHandler(handler)
            handler.close()
    except (OSError, ValueError, TypeError) as exc:
        print(f"Configuration/report error: {exc}")
        return 2
    for result in results:
        print(f"{result['status']:5} {result['scenario']}")
    print(f"SIMULATION ONLY | {counts} | {args.output_dir / 'report.html'}")
    return 2 if counts["error"] else 1 if counts["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
