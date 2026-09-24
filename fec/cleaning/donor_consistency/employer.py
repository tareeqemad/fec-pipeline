"""Employer-field fixes: same-donor spelling unification, the refusal sweep and the fill steps."""
import re
from difflib import SequenceMatcher

import pandas as pd

from fec.cleaning._helpers import levenshtein
from fec.config.constants import (
    SKIP_EMPLOYERS, RAW_JUNK_EMPLOYERS, RAW_STATUS_MAP,
    JUNK_EMPLOYER_RE, REFUSAL_EMPLOYERS, SECTOR_AS_EMPLOYER, ADMIN_NOTE_EMPLOYER_RE,
    ROLE_AS_EMPLOYER, OCCUPATION_AS_EMPLOYER, STATUS_WORDS,
)
from fec.config.occupation_rules.rules import (
    EMPLOYER_FROM_CATEGORY,
    FINAL_EMPLOYER_FROM_OCCUPATION,
)
from fec.env import RAW_CSV
from fec.log import get_logger, log_count

logger = get_logger(__name__)

# the config patterns are plain strings; compile once for the .str calls below
_JUNK_RE = re.compile(JUNK_EMPLOYER_RE)
_ADMIN_NOTE_RE = re.compile(ADMIN_NOTE_EMPLOYER_RE)


def _donor_employer_groups(df: pd.DataFrame):
    """Yield (donor_key, real employers, counts) per donor."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].dropna().unique()
        real = [e for e in emps if e not in SKIP_EMPLOYERS]
        if len(real) < 2:
            continue
        yield dk, real, grp['contributor_employer'].value_counts()


def _employer_typos(df: pd.DataFrame) -> int:
    """Same donor: near-identical employer spellings converge."""
    n_fixed = 0
    for dk, real, counts in _donor_employer_groups(df):
        canonical = max(real, key=lambda e: counts[e])
        cc = counts[canonical]

        for other in real:
            if other == canonical:
                continue
            oc = counts[other]
            if oc >= cc:
                continue
            other_u, canonical_u = other.upper(), canonical.upper()
            lev_ok = (levenshtein(other_u, canonical_u) <= 2
                      and cc / oc >= 3)
            fuzzy_ok = SequenceMatcher(
                None, other_u, canonical_u).ratio() * 100 >= 90
            if lev_ok or fuzzy_ok:
                mask = (df['donor_key'] == dk) & (df['contributor_employer'] == other)
                df.loc[mask, 'contributor_employer'] = canonical
                n_fixed += int(mask.sum())
    return n_fixed


_PARENT_BRAND_MIN_DONORS = 5     # a short name that this many people file on its own is a real parent (NYU), not a truncation
_ACRONYM_STOP = {'OF', 'THE', 'AND', 'FOR', 'IN', 'AT', 'DE', 'LA', 'LLC', 'INC', 'LLP', 'CORP', 'LTD', 'PC', 'LP', 'PLLC'}


def _standalone_donor_counts(df: pd.DataFrame) -> dict:
    """employer -> number of distinct donors who file exactly that name."""
    indiv = df[(df['entity_type'] == 'INDIVIDUAL') & df['contributor_employer'].notna()]
    return indiv.groupby('contributor_employer')['donor_key'].nunique().to_dict()


def _contains_as_words(short: str, long_: str) -> bool:
    return f' {short.upper()} ' in f' {long_.upper()} '


def _employer_substring_variants(df: pd.DataFrame) -> int:
    """Same donor, one name inside the other as whole words (SYNERGY / SYNERGY HEALTH PARTNERS): one company.

    The full name wins because the short one is a truncation of it, except when the short
    name is a parent brand that many people file on its own (NYU next to NYU LANGONE HEALTH
    HUNTINGTON MEDICAL): then the division folds into the parent, as the employer rules do."""
    standalone = _standalone_donor_counts(df)
    n_fixed = 0
    for dk, real, counts in _donor_employer_groups(df):
        fixed_in_group = set()
        for i, a in enumerate(real):
            for b in real[i + 1:]:
                if a in fixed_in_group or b in fixed_in_group:
                    continue
                if _contains_as_words(a, b):
                    short, long_ = a, b
                elif _contains_as_words(b, a):
                    short, long_ = b, a
                else:
                    continue
                winner = short if standalone.get(short, 0) >= _PARENT_BRAND_MIN_DONORS else long_
                loser = long_ if winner == short else short
                mask = (df['donor_key'] == dk) & (df['contributor_employer'] == loser)
                n = int(mask.sum())
                if n:
                    df.loc[mask, 'contributor_employer'] = winner
                    fixed_in_group.add(loser)
                    n_fixed += n
    return n_fixed


def _acronym_of(name: str) -> tuple[str, str]:
    tokens = re.findall(r'[A-Z0-9&]+', name.upper())
    return (''.join(t[0] for t in tokens if t not in _ACRONYM_STOP),
            ''.join(t[0] for t in tokens))


def _employer_acronym_variants(df: pd.DataFrame) -> int:
    """Same donor, an acronym next to the name it abbreviates (WPCM / WHITE PINE CAPITAL MANAGEMENT): the full name wins."""
    n_fixed = 0
    for dk, real, counts in _donor_employer_groups(df):
        longs = [e for e in real if len(re.findall(r'[A-Z0-9&]+', e.upper())) >= 2]
        for short in real:
            s = re.sub(r'[^A-Z0-9&]', '', short.upper())
            if not (2 <= len(s) <= 6) or ' ' in short.strip() and len(short.split()) > 2:
                continue
            for long_ in longs:
                if long_ == short or len(long_) <= len(short):
                    continue
                if s in _acronym_of(long_):
                    mask = (df['donor_key'] == dk) & (df['contributor_employer'] == short)
                    n = int(mask.sum())
                    if n:
                        df.loc[mask, 'contributor_employer'] = long_
                        n_fixed += n
                    break
    return n_fixed


def _fill_employer_from_donor(df: pd.DataFrame) -> int:
    """AI. Fill NaN employer from same donor's other records (needs donor_key).

    Occupation recovery belongs to ``_fill_occupation_from_donor``. Keeping
    the two operations separate prevents one inferred value from triggering a
    second, less reliable inference.
    """
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    explicit_status = (
        indiv['contributor_occupation'].fillna('').str.upper().isin(STATUS_WORDS)
        | indiv['occupation_category'].fillna('').str.upper().isin(STATUS_WORDS)
    )
    null_emp = indiv[indiv['contributor_employer'].isna() & ~explicit_status]
    if null_emp.empty:
        return 0

    dates = pd.to_datetime(df['contribution_receipt_date'], errors='coerce')
    n_fixed = 0
    for dk, missing in null_emp.groupby('donor_key'):
        all_recs = df[(df['donor_key'] == dk) & (df['entity_type'] == 'INDIVIDUAL')]
        real_recs = all_recs[all_recs['contributor_employer'].notna()
                             & ~all_recs['contributor_employer'].isin(SKIP_EMPLOYERS)]
        if real_recs.empty:
            continue
        real_recs = real_recs.assign(_date=dates.loc[real_recs.index]).sort_values(
            '_date', na_position='first', kind='stable')

        for index in missing.index:
            df.at[index, 'contributor_employer'] = _employer_nearest_in_time(real_recs, dates.at[index])
            df.at[index, 'occupation_status'] = 'DERIVED'
        n_fixed += len(missing)

    return n_fixed


def _employer_nearest_in_time(real_recs: pd.DataFrame, when) -> str:
    """The employer the donor filed last on or before `when`, else the first one after it.

    real_recs is sorted by date. A filing with no date takes the latest employer.
    """
    if pd.notna(when):
        dated = real_recs[real_recs['_date'].notna()]
        before = dated[dated['_date'] <= when]
        if not before.empty:
            return before['contributor_employer'].iloc[-1]
        after = dated[dated['_date'] > when]
        if not after.empty:
            return after['contributor_employer'].iloc[0]
    return real_recs['contributor_employer'].iloc[-1]


def _fill_employer_from_occupation(df: pd.DataFrame) -> int:
    """AK. Empty employer + status-word occupation/category -> employer = that status; else recover from raw."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    empty_emp = df['contributor_employer'].isna() | (df['contributor_employer'] == '')
    occ = df['contributor_occupation'].fillna('')

    n = 0
    for occ_val, emp_val in FINAL_EMPLOYER_FROM_OCCUPATION.items():
        mask = is_indiv & empty_emp & (occ == occ_val)
        cnt = int(mask.sum())
        if cnt:
            df.loc[mask, 'contributor_employer'] = emp_val
            n += cnt

    # Still empty? Set from occupation_category
    still_empty = is_indiv & (df['contributor_employer'].isna() | (df['contributor_employer'] == ''))
    for cat, emp_val in EMPLOYER_FROM_CATEGORY.items():
        mask = still_empty & (df['occupation_category'] == cat)
        cnt = int(mask.sum())
        if cnt:
            df.loc[mask, 'contributor_employer'] = emp_val
            n += cnt

    # Still empty? Try to recover from raw FEC data
    still_empty2 = is_indiv & (df['contributor_employer'].isna() | (df['contributor_employer'] == ''))
    if still_empty2.any():
        n += _fill_employer_from_raw(df, still_empty2)

    return n


def _fill_employer_from_raw(df: pd.DataFrame, empty_mask: pd.Series) -> int:
    """Recover an employer from the same donor's own raw filings (their sub_ids).

    Only the donor's own filings count: two people with one name in one state
    (a Los Angeles and a San Francisco COHEN, ROBERT) never share an employer.
    """
    if not RAW_CSV.exists():
        return 0

    raw = pd.read_csv(
        RAW_CSV,
        dtype=str,
        usecols=['sub_id', 'contributor_employer'],
        low_memory=False,
        keep_default_na=False,
    )
    raw_employer = raw.drop_duplicates('sub_id').set_index('sub_id')['contributor_employer']

    donors = set(df.loc[empty_mask, 'donor_key'].dropna())
    own = df[df['donor_key'].isin(donors)]
    filed = own['sub_id'].astype(str).map(raw_employer)

    key_to_emp = {}
    for donor_key, emps in filed.groupby(own['donor_key']):
        employer = _employer_from_raw_filings(emps.fillna('').str.strip())
        if employer is not None:
            key_to_emp[donor_key] = employer

    n = 0
    for idx in df[empty_mask].index:
        donor_key = df.at[idx, 'donor_key']
        if donor_key in key_to_emp:
            df.at[idx, 'contributor_employer'] = key_to_emp[donor_key]
            df.at[idx, 'occupation_status'] = 'DERIVED'   # recovered from donor's raw filings
            n += 1

    log_count(logger, "recover employers from raw", n)
    return n


# sector/role/title/refusal words are blanked or converted upstream on
# purpose - never re-recover them from raw
_NEVER_RECOVERED = frozenset().union(
    RAW_JUNK_EMPLOYERS, RAW_STATUS_MAP.keys(), SECTOR_AS_EMPLOYER,
    ROLE_AS_EMPLOYER, OCCUPATION_AS_EMPLOYER, REFUSAL_EMPLOYERS,
)


def _employer_from_raw_filings(emps: pd.Series):
    """One donor's most filed real raw employer, else status word."""
    emps_u = emps.str.upper()
    real = emps[~emps_u.isin(_NEVER_RECOVERED) & (emps.str.len() > 2)]
    real_u = real.str.upper()
    # same structural-junk patterns the cleaner uses (emails, dates, masked
    # digits, admin notes) so junk blanked upstream is not re-recovered
    real = real[~real.str.contains('@', na=False, regex=False)
                & ~real_u.str.match(_JUNK_RE, na=False)
                & ~real_u.str.match(_ADMIN_NOTE_RE, na=False)]
    if len(real) > 0:
        return real.value_counts().index[0]

    status = emps[emps_u.isin(RAW_STATUS_MAP.keys())]
    if len(status) > 0:
        raw_val = status.value_counts().index[0].upper()
        return RAW_STATUS_MAP.get(raw_val, raw_val)
    return None


_OWN_FIRM_TOKEN_RE = re.compile(
    r'\b(?:LLC|LLP|INC|CORP|CORPORATION|CO|COMPANY|COMPANIES|LTD|LP|PLLC|PC|PA|LAW|CPA|MD|DDS|DMD|ESQ|'
    r'OFFICES?|GROUP|PARTNERS|ASSOCIATES|ADVISORS|CONSULTING|CONSULTANTS|DESIGN|STUDIO|CONSTRUCTION|'
    r'REALTY|PROPERTIES|HOLDINGS|ENTERPRISES|INVESTMENTS|CAPITAL|MANAGEMENT|MEDICAL|DENTAL|CLINIC|'
    r'INSURANCE|FINANCIAL|ARCHITECTS?|ENGINEERING|BUILDERS|HOMES|FARMS?|&)\b'
)


def _own_firm_absorbs_self_employed(df: pd.DataFrame) -> int:
    """AV. A donor who files both SELF-EMPLOYED and a firm carrying their own surname
    (SCOTT FANE CPA PA, SCHALL LAW FIRM, GENET PROPERTY GROUP) has one workplace: the firm.
    The firm must read as a business, so a joint personal name (GEORGE AND LEESA WEISZ) never wins."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0
    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].dropna()
        if not (emps == 'SELF-EMPLOYED').any():
            continue
        surname = str(grp['contributor_name'].iloc[0]).split(',')[0].strip().upper()
        if len(surname) < 4:
            continue
        firms = [
            e for e in emps.unique()
            if e and e != 'SELF-EMPLOYED' and e not in SKIP_EMPLOYERS
            and re.search(rf'\b{re.escape(surname)}\b', e.upper())
            and _OWN_FIRM_TOKEN_RE.search(e.upper())
        ]
        if len(firms) != 1:
            continue
        mask = (df['donor_key'] == dk) & (df['contributor_employer'] == 'SELF-EMPLOYED')
        n = int(mask.sum())
        if n:
            df.loc[mask, 'contributor_employer'] = firms[0]
            n_fixed += n
    return n_fixed
