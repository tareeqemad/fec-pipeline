"""Scoring weights, nickname map, and look-alike names."""

# occupations too generic to corroborate a match
GENERIC_OCC_CATEGORIES = {
    "RETIRED", "NOT EMPLOYED", "SELF-EMPLOYED", "OTHER",
    "ORGANIZATION", "POLITICAL COMMITTEE", "HOMEMAKER", "STUDENT",
    "NAN", "",
}

NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "ESQ", "MD", "PHD", "DDS", "CPA", "DO"}


# all scoring weights; merge at MERGE_THRESHOLD

# shared evidence
SCORE_SAME_STREET    = 60   # strongest single proof
SCORE_SAME_EMPLOYER  = 30   # same real employer (status words never count)
SCORE_SAME_STATE     = 10   # weak alone; many share a state
SCORE_SAME_CITY      = 15
SCORE_SAME_ZIP5      = 25   # exact ZIP
SCORE_SAME_ZIP3      = 5    # same ZIP area (first 3 digits)
SCORE_SAME_OCC       = 15   # same occupation category
SCORE_OCC_RETIRED    = 10   # career on one side, RETIRED on other

# middle names: only evidence that hard-blocks
SCORE_MIDDLE_MATCH   = 15   # same, matching initial, or 1-char typo
SCORE_MIDDLE_PARTIAL = 5    # only one side has a middle
SCORE_MIDDLE_CONFLICT = -30 # different initials: suspicious, not fatal

# name rarity by records sharing the name
SCORE_RARE_NAME      = 15   # freq <= 3
SCORE_VERY_RARE_BONUS = 20  # freq <= 2, stacks with RARE_NAME
SCORE_COMMON_PENALTY = -15  # freq > 10 needs more proof

# situation bonuses
SCORE_CROSS_NAME_BONUS = 10  # nickname/typo first names, only with corroboration
SCORE_RETIRED_NO_EMP   = 25  # both unemployed, rare name, shared geography
SCORE_RARE_EMPLOYER_OK = 10  # no geography, shared employer, rare name

MERGE_THRESHOLD = 50

# similar first names that are different people
BLOCKED_FIRST_PAIRS = {frozenset(p) for p in [
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
