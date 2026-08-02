"""Constants for donor matching: scoring weights, nickname map, blocked pairs."""

import csv
from collections import defaultdict
from pathlib import Path

from fec.config.constants import EMPLOYER_STATUS_VALUES

# employer statuses that are not real companies
STATUS_EMPLOYERS = EMPLOYER_STATUS_VALUES

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


def _load_do_not_merge() -> set:
    """Read curated 'not the same person' name pairs from data/database/donor_no_merge.csv."""
    path = Path(__file__).resolve().parents[3] / "data" / "database" / "donor_no_merge.csv"
    if not path.exists():
        return set()
    pairs: set = set()
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            a, b = _norm_blk(row.get("name_a") or ""), _norm_blk(row.get("name_b") or "")
            if a and b:
                pairs.add(frozenset((a, b)))
    return pairs


_DNM_SET = _load_do_not_merge()


def _donor_overrides_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "database" / "donor_overrides.csv"


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


# scoring weights
SCORE_CROSS_NAME_BONUS = 10
SCORE_SAME_STREET    = 60
SCORE_SAME_EMPLOYER  = 30
SCORE_SAME_STATE     = 10
SCORE_SAME_CITY      = 15
SCORE_SAME_ZIP5      = 25
SCORE_SAME_ZIP3      = 5
SCORE_MIDDLE_MATCH   = 15
SCORE_MIDDLE_PARTIAL = 5
SCORE_MIDDLE_CONFLICT = -30
SCORE_RARE_NAME      = 15
SCORE_VERY_RARE_BONUS = 20
SCORE_COMMON_PENALTY = -15
SCORE_SAME_OCC       = 15
SCORE_OCC_RETIRED    = 10

# cross-state merge (no geographic anchor) needs a distinctive name: few
# distinct full names sharing the surname (or first name)
XSTATE_LAST_RARE_MAX  = 5
XSTATE_FIRST_RARE_MAX = 6

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
