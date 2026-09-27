"""Move files left in the old flat data/ layout into data/raw, rules, cache, output and reports.

Run once after pulling the commit that split data/. Git moves the files it tracks; this
moves the rest (audit files, review queues, the FEC source fields, data/_review). A file
whose new place is already taken is left where it is and listed, never overwritten.

    python tools/move_data_layout.py            # move
    python tools/move_data_layout.py --dry-run  # only list
"""
import argparse
import shutil
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"

RAW = ("contributions.csv", "fec_source_fields.csv", "pull_state.json")
CACHE = ("geocode_cache.json", "fec_address_cache.json", "resolve_employer_addr.json", "resolve_prev_employer.json")
OUTPUT = ("contributions_cleaned.csv", "employer_locations.csv", "pipeline_run.json")
RULES = ("manual_employer_overrides.csv", "manual_employer_addresses.csv")
REPORTS = (
    "amount_flags.csv", "audit_changes.csv", "audit_summary.json", "auto_city_fixes.json",
    "missing_report.csv", "quality_gates.json", "quality_scan.json", "employer_address_review.csv",
    "address_manual_review.csv", "address_regeocode_suspects.csv", "address_review_cases.csv",
    "donor_dedup_review.csv",
)


# old path -> new path for every file the split moved
def _moves() -> list[tuple[Path, Path]]:
    moves = [(DATA / name, DATA / folder / name)
             for folder, names in (("raw", RAW), ("cache", CACHE), ("output", OUTPUT),
                                   ("rules", RULES), ("reports", REPORTS))
             for name in names]
    old_rules = DATA / "database"
    if old_rules.is_dir():
        moves += [(path, DATA / "rules" / path.name) for path in sorted(old_rules.iterdir()) if path.is_file()]
    old_review = DATA / "_review"
    if old_review.is_dir():
        moves += [(path, DATA / "reports" / "review" / path.relative_to(old_review))
                  for path in sorted(old_review.rglob("*")) if path.is_file()]
    return moves


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="list the moves without making them")
    args = parser.parse_args(argv)

    moved, kept = 0, []
    for old, new in _moves():
        if not old.exists():
            continue
        if new.exists():
            kept.append((old, new))
            continue
        print(f"{'would move' if args.dry_run else 'moved'} {old.relative_to(DATA.parent)} -> {new.relative_to(DATA.parent)}")
        if not args.dry_run:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
        moved += 1
    if not args.dry_run:
        for folder in (DATA / "database", DATA / "_review"):
            if folder.is_dir() and not any(path.is_file() for path in folder.rglob("*")):
                shutil.rmtree(folder)
    for old, new in kept:
        print(f"LEFT {old.relative_to(DATA.parent)}: {new.relative_to(DATA.parent)} already exists; compare them by hand")
    print(f"{moved} file(s) {'to move' if args.dry_run else 'moved'}, {len(kept)} left for you to compare")
    return 1 if kept else 0


if __name__ == "__main__":
    sys.exit(main())
