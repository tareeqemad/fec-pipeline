"""Employer-field fixes: same-donor spelling unification, the refusal sweep and the fill steps."""
import re

import pandas as pd

from fec.cleaning.occupations import _categorize_final
from fec.config.constants import (
    SKIP_EMPLOYERS, RAW_JUNK_EMPLOYERS, RAW_STATUS_MAP, JUNK_EMPLOYER_RE,
    REFUSAL_EMPLOYERS, ADMIN_NOTE_EMPLOYER_RE, STATUS_WORDS,
)
from fec.config.not_employers import (
    SECTOR_AS_EMPLOYER, ROLE_AS_EMPLOYER, OCCUPATION_AS_EMPLOYER,
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
# compiled admin-note/refusal phrasing: "PENDING", "DECLINED TO STATE"
_ADMIN_NOTE_RE = re.compile(ADMIN_NOTE_EMPLOYER_RE)


# fill missing employer from donor's nearest other record
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


# pick employer filed closest in time to a given date
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


# derive employer from a status-word occupation or category
def _fill_employer_from_occupation(df: pd.DataFrame) -> int:
    """AK. Empty employer + status-word occupation/category -> employer = that status."""
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

    return n


# fill any still-empty employer from the donor's raw filings
def _fill_employer_from_raw_filings(df: pd.DataFrame) -> int:
    """AK2. A still-empty employer from the donor's own other raw filings."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    empty = is_indiv & (df['contributor_employer'].isna() | (df['contributor_employer'] == ''))
    return _fill_employer_from_raw(df, empty) if empty.any() else 0


# load raw CSV, recover employer from donor's own sub_ids
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


# pick donor's most common real employer, else a status word
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


# a business/professional-firm token: "LLC", "CPA", "LAW", "GROUP"
_OWN_FIRM_TOKEN_RE = re.compile(
    r'\b(?:LLC|LLP|INC|CORP|CORPORATION|CO|COMPANY|COMPANIES|LTD|LP|PLLC|PC|PA|LAW|CPA|MD|DDS|DMD|ESQ|'
    r'OFFICES?|GROUP|PARTNERS|ASSOCIATES|ADVISORS|CONSULTING|CONSULTANTS|DESIGN|STUDIO|CONSTRUCTION|'
    r'REALTY|PROPERTIES|HOLDINGS|ENTERPRISES|INVESTMENTS|CAPITAL|MANAGEMENT|MEDICAL|DENTAL|CLINIC|'
    r'INSURANCE|FINANCIAL|ARCHITECTS?|ENGINEERING|BUILDERS|HOMES|FARMS?|&)\b'
)


# categories that name a title or status, not a field of work
_NO_FIELD = frozenset({'OTHER', 'SELF-EMPLOYED', 'EXECUTIVE / C-SUITE', 'BUSINESS / ENTREPRENEUR'})


# move self-employed rows to donor's own firm by surname
def _own_firm_absorbs_self_employed(df: pd.DataFrame) -> int:
    """AV. A donor who files both SELF-EMPLOYED and a firm carrying their own surname
    (SCOTT FANE CPA PA, SCHALL LAW FIRM, GENET PROPERTY GROUP) has one workplace: the firm.
    The firm must read as a business, so a joint personal name (GEORGE AND LEESA WEISZ) never wins.
    Only a self-employed filing in a field filed at the firm (or with no field) moves:
    FOLDES, NADINE's SELF / SOCIAL WORKER filing is not her family's wealth firm."""
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
        donor_rows = df['donor_key'] == dk
        # the field of each filing's own occupation (a SELF-EMPLOYED filing's
        # category still reads SELF-EMPLOYED here, so it cannot be compared)
        occupation = df.loc[donor_rows, 'contributor_occupation'].fillna('')
        field = _categorize_final(occupation)
        firm_fields = set(field[df.loc[donor_rows, 'contributor_employer'] == firms[0]]) - _NO_FIELD
        same_field = occupation.eq('') | field.isin(_NO_FIELD) | field.isin(firm_fields)
        if not firm_fields:
            same_field[:] = True
        mask = donor_rows & (df['contributor_employer'] == 'SELF-EMPLOYED')
        mask &= same_field.reindex(df.index, fill_value=False)
        n = int(mask.sum())
        if n:
            df.loc[mask, 'contributor_employer'] = firms[0]
            n_fixed += n
    return n_fixed
