"""Constants for donor matching: scoring weights, nickname map, blocked pairs."""

import csv
from collections import defaultdict
from pathlib import Path

from fec.env import DATA_DIR

# occupation buckets too generic to corroborate a match (unlike a real field
# such as LEGAL); RETIRED is also tracked separately as a transition state
GENERIC_OCC_CATEGORIES = {
    "RETIRED", "NOT EMPLOYED", "SELF-EMPLOYED", "OTHER",
    "ORGANIZATION", "POLITICAL COMMITTEE", "HOMEMAKER", "STUDENT",
    "NAN", "",
}

NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "ESQ", "MD", "PHD", "DDS", "CPA", "DO"}


def _norm_blk(s: str) -> str:
    """Whitespace/case-normalize a full name for do-not-merge comparison."""
    return " ".join(str(s).upper().split())


def _has_location(row: dict) -> bool:
    """Return whether a do-not-merge row identifies two locations."""
    fields = ("city_a", "state_a", "city_b", "state_b")
    return all((row.get(field) or "").strip() for field in fields)


def _identity(name: str, city: str, state: str) -> str:
    """Build the normalized identity used by location-specific blocks."""
    return "|".join((_norm_blk(name), _norm_blk(city), _norm_blk(state)))


def _load_do_not_merge() -> set:
    """Read curated name pairs that must never merge."""
    path = DATA_DIR / "database" / "donor_no_merge.csv"
    if not path.exists():
        return set()
    pairs: set = set()
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if _has_location(row):
                continue
            a, b = _norm_blk(row.get("name_a") or ""), _norm_blk(row.get("name_b") or "")
            if a and b:
                pairs.add(frozenset((a, b)))
    return pairs


_DNM_SET = _load_do_not_merge()


def _load_identity_do_not_merge() -> set:
    """Read same-name blocks scoped to exact city/state pairs."""
    path = DATA_DIR / "database" / "donor_no_merge.csv"
    if not path.exists():
        return set()
    pairs: set = set()
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if not _has_location(row):
                continue
            a = _identity(row["name_a"], row["city_a"], row["state_a"])
            b = _identity(row["name_b"], row["city_b"], row["state_b"])
            pairs.add(frozenset((a, b)))
    return pairs


_DNM_IDENTITY_SET = _load_identity_do_not_merge()


def _donor_overrides_path() -> Path:
    return DATA_DIR / "database" / "donor_overrides.csv"


def _load_force_merge_names() -> set:
    """Read (last, first) pairs from donor_overrides.csv, quiet on missing file; rows with a group value belong to _load_force_merge_groups and are skipped."""
    path = _donor_overrides_path()
    if not path.exists():
        return set()
    pairs: set = set()
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("group") or "").strip():
                continue
            last = (row.get("last_name") or "").strip().upper()
            first = (row.get("first_name") or "").strip().upper()
            if last and first:
                pairs.add((last, first))
    return pairs


def _load_force_merge_groups() -> dict:
    """Read {group_id: [(LAST, FIRST), ...]} cross-surname same-person groups from donor_overrides.csv; the human-verified escape hatch, since the matcher never compares across surnames."""
    path = _donor_overrides_path()
    if not path.exists():
        return {}
    groups: dict = defaultdict(list)
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            grp = (row.get("group") or "").strip()
            last = (row.get("last_name") or "").strip().upper()
            first = (row.get("first_name") or "").strip().upper()
            if grp and last and first:
                groups[grp].append((last, first))
    return {g: ids for g, ids in groups.items() if len(ids) >= 2}


FORCE_MERGE_NAMES = _load_force_merge_names()
FORCE_MERGE_GROUPS = _load_force_merge_groups()


def _is_blocked_merge(name1: str, name2: str) -> bool:
    """Check if two names are in the do-not-merge list (case/space-normalized)."""
    return frozenset((_norm_blk(name1), _norm_blk(name2))) in _DNM_SET


def _is_blocked_identity(person1: dict, person2: dict) -> bool:
    """Check both name-only and location-specific separation rules."""
    if _is_blocked_merge(person1["name"], person2["name"]):
        return True
    pair = frozenset((
        _identity(person1["name"], person1["city"], person1["state"]),
        _identity(person2["name"], person2["city"], person2["state"]),
    ))
    return pair in _DNM_IDENTITY_SET


# Scoring weights - ALL of them, in one place. compute_score adds up evidence
# that two records are the same person; a pair MERGES at >= MERGE_THRESHOLD.
# Worked examples: same street alone (60) merges. Same employer + same state
# (40) does not. Employer + state + city (55) does. A middle-initial conflict
# (-30) sinks that back to 25.

# shared evidence
SCORE_SAME_STREET    = 60   # same street on file - the strongest single proof
SCORE_SAME_EMPLOYER  = 30   # same real employer (status words never count)
SCORE_SAME_STATE     = 10   # weak alone - half a state shares it
SCORE_SAME_CITY      = 15
SCORE_SAME_ZIP5      = 25   # exact ZIP
SCORE_SAME_ZIP3      = 5    # same ZIP area (first 3 digits)
SCORE_SAME_OCC       = 15   # same occupation category
SCORE_OCC_RETIRED    = 10   # one side a career, the other RETIRED - a plausible transition

# middle names - the ONLY evidence that can hard-block: two different full
# middle names force the score to -999 (two different people, never merge)
SCORE_MIDDLE_MATCH   = 15   # same middle, initial matching the full form, or 1-char typo
SCORE_MIDDLE_PARTIAL = 5    # only one side has a middle name - no contradiction
SCORE_MIDDLE_CONFLICT = -30 # different INITIALS (J vs M) - suspicious, not fatal

# name rarity (name_freq = distinct records sharing the normalized name)
SCORE_RARE_NAME      = 15   # freq <= 3
SCORE_VERY_RARE_BONUS = 20  # freq <= 2, stacks with RARE_NAME (+35 total)
SCORE_COMMON_PENALTY = -15  # freq > 10: common names need more proof

# situation bonuses
SCORE_CROSS_NAME_BONUS = 10  # nickname/typo first-name pair (phase 2), only with corroboration
SCORE_RETIRED_NO_EMP   = 25  # neither side employed + rare name + shared geography
SCORE_RARE_EMPLOYER_OK = 10  # no geography at all, but shared employer + rare name

MERGE_THRESHOLD = 50

# blocked first-name pairs: look similar but are different people
_BLOCKED_FIRST_PAIRS = {frozenset(p) for p in [
    ('DAN', 'DANA'),        ('DANIEL', 'DANA'),
    ('BETH', 'SETH'),
    ('JOAN', 'JOHN'),
    ('ANDREA', 'ANDREW'),   ('ANDREA', 'ANDRE'),
    ('ALLEN', 'ELLEN'),     ('ALAN', 'ELENA'),
    ('RANDI', 'RANDY'),
    ('ROBIN', 'ROB'),       ('ROBIN', 'ROBERT'),
    ('SANDY', 'ANDY'),      ('SANDRA', 'ANDREW'),
    ('CARL', 'CARI'),
    ('EVAN', 'IVAN'),
    ('MARK', 'MARY'),       ('MARC', 'MARY'),
    ('KEREN', 'KAREN'),
    ('EARL', 'CARL'),
    ('JOY', 'JAY'),
    ('DAWN', 'DAN'),
    ('JANE', 'JUNE'),
    ('JULIE', 'JULIA'),
    ('ANN', 'DAN'),
    ('BARRY', 'LARRY'),     ('BARRY', 'HARRY'),
    ('LARRY', 'HARRY'),     ('TERRY', 'JERRY'),
    ('TERRY', 'PERRY'),     ('TERRY', 'KERRY'),
    ('JERRY', 'PERRY'),     ('JERRY', 'KERRY'),
    ('LOREN', 'OREN'),
    ('ARI', 'AVI'),         ('ARI', 'URI'),
    ('ETAN', 'EVAN'),
    ('KEN', 'BEN'),         ('KEN', 'LEN'),
    ('BEN', 'LEN'),
    ('ROY', 'ROB'),         ('ROY', 'RON'),
    ('GARY', 'CARY'),
]}


NICKNAME_MAP = {
    'BILL': 'WILLIAM', 'BILLY': 'WILLIAM', 'WILL': 'WILLIAM',
    'BOB': 'ROBERT', 'BOBBY': 'ROBERT', 'ROB': 'ROBERT',
    'DICK': 'RICHARD', 'RICK': 'RICHARD', 'RICH': 'RICHARD',
    'JIM': 'JAMES', 'JIMMY': 'JAMES',
    'MIKE': 'MICHAEL',
    'DAVE': 'DAVID',
    'DAN': 'DANIEL', 'DANNY': 'DANIEL',
    'TOM': 'THOMAS', 'TOMMY': 'THOMAS',
    'STEVE': 'STEVEN', 'STEPHEN': 'STEVEN',
    'JOE': 'JOSEPH', 'JOEY': 'JOSEPH',
    'JACK': 'JOHN', 'JON': 'JOHN',
    'CHRIS': 'CHRISTOPHER',
    'TONY': 'ANTHONY',
    'ED': 'EDWARD', 'EDDIE': 'EDWARD', 'TED': 'EDWARD',
    'KEN': 'KENNETH', 'KENNY': 'KENNETH',
    'LARRY': 'LAWRENCE',
    'AL': 'ALAN',
    'MANNY': 'MANUEL',
    'MARV': 'MARVIN',
    'PHIL': 'PHILIP', 'PHILLIP': 'PHILIP',
    'JEFF': 'JEFFREY',
    'GREG': 'GREGORY',
    'BEN': 'BENJAMIN',
    'SAM': 'SAMUEL',
    'FRED': 'FREDERICK', 'FREDRIC': 'FREDERICK',
    'BETH': 'ELIZABETH', 'LIZ': 'ELIZABETH', 'BETSY': 'ELIZABETH',
    'SUE': 'SUSAN', 'SUSIE': 'SUSAN',
    'PAT': 'PATRICIA', 'PATTY': 'PATRICIA',
    'SANDY': 'SANDRA',
    'KATE': 'KATHERINE', 'KATHY': 'KATHERINE',
    'DEB': 'DEBORAH', 'DEBBIE': 'DEBORAH',
    'BARB': 'BARBARA',
    'JEN': 'JENNIFER', 'JENNY': 'JENNIFER',
    'BECKY': 'REBECCA',
    'MAGGIE': 'MARGARET', 'PEGGY': 'MARGARET',
    'NICK': 'NICHOLAS',
    'MATT': 'MATTHEW',
    'ANDY': 'ANDREW', 'DREW': 'ANDREW',
    'ALEX': 'ALEXANDER',
    'CHARLIE': 'CHARLES', 'CHUCK': 'CHARLES',
    'JERRY': 'GERALD',
    'RON': 'RONALD', 'RONNIE': 'RONALD',
    'DOUG': 'DOUGLAS',
    'RAY': 'RAYMOND',
    'HANK': 'HENRY',
    'ABE': 'ABRAHAM',
    'LEN': 'LEONARD', 'LENNY': 'LEONARD',
    'BERNIE': 'BERNARD',
    'ART': 'ARTHUR',
    'NORM': 'NORMAN',
    'HERB': 'HERBERT',
    'STU': 'STUART',
    'MORT': 'MORTON',
    'JOSH': 'JOSHUA',
}
