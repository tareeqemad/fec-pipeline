#!/usr/bin/env python3
"""run.py — Run the full FEC pipeline in one command.

    python run.py

Steps:
  1. clean.py                    (clean + match + canonicalize)
  2. geocode.py                  (donor addresses)
  3. resolve.py --apply          (employer/committee addresses)
  4. geocode.py --employer-only  (employer coordinates)
  5. build_employers.py          (employers.csv dimension)

All steps cache their work. If interrupted, re-run and it resumes.
Load into the database afterwards with: python loader.py --reset

Options:
  --skip-clean / --skip-geocode / --skip-resolve
  --dry-run            Print the commands without running them
"""

import argparse
import subprocess
import sys
import time
import os

from fec.log import get_logger

logger = get_logger(__name__)


def run_step(name: str, cmd: list[str], dry_run: bool = False) -> bool:
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"  Step: {name}")
    logger.info("=" * 60)

    if dry_run:
        logger.info("  [dry-run] Would run: " + " ".join(cmd))
        return True

    start = time.time()
    result = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))
    m, s = divmod(int(time.time() - start), 60)
    ok = result.returncode == 0
    logger.info(f"\n  {'ok' if ok else 'FAILED'}: {name} — {m}m {s}s")

    if not ok:
        logger.info("  Fix the issue and re-run: python run.py")
        logger.info("  Completed steps are cached — they won't repeat.")
    return ok


def main():
    p = argparse.ArgumentParser(description="Run full FEC pipeline")
    p.add_argument("input", nargs="?", default=None,
                   help="Raw CSV path (default: data/contributions.csv)")
    p.add_argument("--skip-clean", action="store_true")
    p.add_argument("--skip-geocode", action="store_true")
    p.add_argument("--skip-resolve", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    py = sys.executable
    total_start = time.time()

    logger.info("")
    logger.info("=" * 60)
    logger.info("  FEC Contributions Pipeline — Full Run")
    logger.info("=" * 60)

    if not args.skip_clean:
        clean_cmd = [py, "clean.py"]
        if args.input:
            clean_cmd.append(args.input)
        if not run_step("Cleaning + Classification", clean_cmd, args.dry_run):
            sys.exit(1)
    else:
        logger.info("\n  Cleaning: skipped (--skip-clean)")

    if not args.skip_geocode:
        if not run_step("Geocode Contributors", [py, "geocode.py"], args.dry_run):
            sys.exit(1)
    else:
        logger.info("\n  Geocoding: skipped (--skip-geocode)")

    if not args.skip_resolve:
        if not run_step("Employer Resolution", [py, "resolve.py", "--apply"], args.dry_run):
            sys.exit(1)
    else:
        logger.info("\n  Resolve: skipped (--skip-resolve)")

    if not args.skip_geocode and not args.skip_resolve:
        if not run_step("Geocode Employers", [py, "geocode.py", "--employer-only"], args.dry_run):
            sys.exit(1)

    if not args.skip_resolve:
        if not run_step("Normalize Employers", [py, "build_employers.py"], args.dry_run):
            sys.exit(1)

    h, rem = divmod(int(time.time() - total_start), 3600)
    m = rem // 60
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"  Pipeline complete — {h}h {m}m")
    logger.info("  Output: data/contributions_cleaned.csv + data/employers.csv")
    logger.info("  Next:   python loader.py --reset")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
