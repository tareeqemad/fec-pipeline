"""Constants for donor matching: scoring weights, nickname map, blocked pairs."""

import csv
from collections import defaultdict
from pathlib import Path

# ── Employer statuses that are NOT real companies ──
STATUS_EMPLOYERS = {
    "RETIRED", "SELF-EMPLOYED", "NOT EMPLOYED", "NOT DISCLOSED",
    "STUDENT", "CAMPAIGN/COMMITTEE", "NONE", "N/A",
    "NAN", "nan", "None", "NA",
}

# ── Occupation categories that don't distinguish a person ──
# A *real* professional field (LEGAL, MEDICAL, FINANCE …) matching across two
# same-name records is corroboration. These generic buckets are not — they're
# either life-status (RETIRED), placeholders, or too broad/common to mean much.
# RETIRED is excluded here but tracked separately as a transition state.
GENERIC_OCC_CATEGORIES = {
    "RETIRED", "NOT EMPLOYED", "SELF-EMPLOYED", "OTHER",
    "ORGANIZATION", "POLITICAL COMMITTEE", "HOMEMAKER", "STUDENT",
    "NAN", "",
}

NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "ESQ", "MD", "PHD", "DDS", "CPA", "DO"}

# ── Do-not-merge / force-merge lists ──
# FORCE_MERGE_NAMES is loaded from data/database/donor_overrides.csv at
# import time. Each entry is a (LAST, FIRST) tuple — every record matching
# that name gets merged into a single donor cluster after the regular
# scoring pass. Use sparingly: it bypasses the geographic-corroboration
# safety net, so only add names that you've verified are the same person.
DO_NOT_MERGE_NAMES = set()


def _norm_blk(s: str) -> str:
    """Whitespace/case-normalize a full name for do-not-merge comparison."""
    return " ".join(str(s).upper().split())


def _load_do_not_merge() -> set:
    """Read full-name pairs flagged 'not the same person' from
    data/database/donor_no_merge.csv (curated during manual merge review).
    Each blocked pair stops the matcher from ever fusing those two names."""
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


_DNM_SET = {frozenset(map(_norm_blk, pair)) for pair in DO_NOT_MERGE_NAMES} | _load_do_not_merge()


def _donor_overrides_path() -> Path:
    # fec/database/donor_match/constants.py → repo root is 4 parents up
    return Path(__file__).resolve().parents[3] / "data" / "database" / "donor_overrides.csv"


def _load_force_merge_names() -> set:
    """Read (last, first) pairs from donor_overrides.csv. Quiet on missing file.

    Rows carrying a non-empty ``group`` are handled by
    :func:`_load_force_merge_groups` instead (they merge ACROSS surnames),
    so they're skipped here to avoid a redundant same-name pass.
    """
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
    """Read cross-surname same-person groups from donor_overrides.csv.

    Rows sharing a non-empty ``group`` value are fused into ONE donor even
    when their surnames are spelled differently (KHODARI / KHODAR / KHIDARI).
    The regular matcher never compares records across different surnames
    (the surname is a hard identity anchor), so this is the manual,
    human-verified escape hatch for confirmed spelling variants.

    Returns ``{group_id: [(LAST, FIRST), ...]}`` for groups of size >= 2.
    """
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


# ── Nickname canonicalization ──
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

# ── Scoring weights ──
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
SCORE_SAME_OCC       = 15   # same real occupation field (LEGAL=LEGAL, …)
SCORE_OCC_RETIRED    = 10   # career → retirement transition (lawyer who retired)

# Cross-state merge (no geographic anchor) is allowed only for a DISTINCTIVE
# name — measured as # of distinct full names sharing the surname / first name.
# A rare surname (≤5, e.g. SHEAR) OR a rare first name (≤6, e.g. BATYA) qualifies;
# common names (MOORE/COHEN/DAVID, hundreds of namesakes) never do.
XSTATE_LAST_RARE_MAX  = 5
XSTATE_FIRST_RARE_MAX = 6

MERGE_THRESHOLD = 50

# ── Blocked first-name pairs (look similar but are different people) ──
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
