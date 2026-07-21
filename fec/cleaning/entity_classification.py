"""
cleaning/entity_classification.py — Entity type reclassification and name corrections.

Extracted from enhancements.py. Contains:
  - fix_remaining_misclassified() — individuals → COMMITTEE/PAC
  - fix_misclassified_business_entities() — business names → COMMITTEE/PAC
  - normalize_business_names() — strip legal suffixes from committee names
  - apply_name_corrections() — manual name fixes
  - fix_double_apostrophes() — CHALME'' → CHALME'
  - fix_null_last_name() — literal NULL string cleanup
  - normalize_name_periods() — I.R. → I R, MR. → MR
  - fix_fullname_in_both_fields() — full name duplicated in first+last
  - fix_employer_equals_occupation() — company name in both fields
  - fix_is_individual_column() — sync is_individual with entity_type
"""
import re
from typing import Tuple

import numpy as np
import pandas as pd

from fec.cleaning._helpers import _norm, _indiv_idx
from fec.config import COMM_PATTERNS


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Constants
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_COMMITTEE_IN_NAME_RE = re.compile(
    r'FRIENDS TO ELECT|CITIZENS TO ELECT|COMMITTEE TO ELECT'
    r'|FRIENDS OF |FRIENDS FOR |CITIZENS FOR |PEOPLE FOR '
    r'|FOR CONGRESS|FOR SENATE|FOR U\.?S\.?\s*SENATE'
    r'|FOR AMERICA|FOR GOVERNOR|FOR MAYOR'
    # ── State-specific campaign names ──
    r'|FOR NC\b|FOR NEVADA\b|FOR TEXAS\b|FOR VIRGINIA\b|FOR COLORADO\b'
    r'|FOR NEW YORK\b|FOR MICHIGAN\b|FOR OHIO\b|FOR CALIFORNIA\b'
    r'|FOR GEORGIA\b|FOR FLORIDA\b|FOR ARIZONA\b|FOR PENNSYLVANIA\b'
    r'|FOR WISCONSIN\b|FOR MISSOURI\b|FOR INDIANA\b|FOR MARYLAND\b'
    r'|FOR MINNESOTA\b|FOR WASHINGTON\b|FOR ILLINOIS\b|FOR OKLAHOMA\b'
    # ── PAC / fund patterns ──
    r'|VICTORY FUND|ACTION FUND|LEADERSHIP PAC|LEADERSHIP FUND'
    # ── Spaceless variants ──
    r'|BELLFORMISSOURI'
    # ── Original patterns ──
    r'|4\s*CONGRESS|4\s*UTAH|4\s*SENATE'
    r'|CAMPAIGN$'
    r'|\.COM$'
    r'|ELECT\w+\.COM',
    re.IGNORECASE,
)

_BUSINESS_SUFFIX_RE = re.compile(
    r'\bLLC\b|\bLLP\b|\bL\.L\.C\b|\bL\.L\.P\b'
    r'|\bINC\b|\bCORP\b|\bLTD\b|\bP\.?L\.?L\.?C\b'
    r'|\bP\.?C\.?\b|\bP\.?A\.?\b',
    re.IGNORECASE
)

# Import centralized regex
from fec.config.constants import LEGAL_SUFFIX_RE as _LEGAL_SUFFIX_RE

_NAME_CORRECTIONS = {
    "CHALME'', RAYMOND": "CHALME, RAYMOND",
    # Organizations the person-style "LAST, FIRST" parse flipped. Corrected by
    # hand rather than by a rule: a comma in an org name is usually legitimate
    # ("JPMORGAN CHASE BANK, N.A.", "RST DEVELOPMENT, LLC"), so auto-flipping
    # every comma'd org name would corrupt more than it repairs.
    #
    # The targets are the entities' REAL names, not a naive un-flip: each is
    # corroborated by the donor's own employer field, their filed address, and
    # an external source. Keep these in sync with data/database/
    # entity_overrides.csv, which pins entity_type by contributor_name — a
    # rename here silently breaks that override if the key isn't updated too.
    #   FIRST CENTRAL SAVINGS BANK  — FDIC bank, 70 Glen St, Glen Cove NY 11542
    #                                 (matches the filed address exactly)
    #   WHITE LAKE REAL ESTATE CAPITAL LLC — Greenwich CT firm (founder Billy
    #                                 Jacobs); matches the filer's employer field
    "BANK, FIRST CENTRAL": "FIRST CENTRAL SAVINGS BANK",
    "CAPITAL, WHITE": "WHITE LAKE REAL ESTATE CAPITAL LLC",
    "KAHAN TRUST, DAVID": "DAVID KAHAN TRUST",
}

_GENERIC_EMP_OCC = frozenset({
    'RETIRED', 'SELF-EMPLOYED', 'NOT EMPLOYED', 'NOT DISCLOSED', 'HOMEMAKER',
    'STUDENT', 'REAL ESTATE', 'FINANCE', 'SALES', 'CONSULTING', 'MANAGEMENT',
    'INSURANCE', 'MARKETING', 'BANKING', 'GOVERNMENT', 'EDUCATION',
    'CONSTRUCTION', 'ACCOUNTING', 'TECHNOLOGY', 'ENGINEERING', 'VOLUNTEER',
    'CASHIER', 'NURSE', 'HEALTHCARE', 'ENTREPRENEUR', 'INDEPENDENT CONTRACTOR',
    'FREELANCE', 'COMMUNITY VOLUNTEER', 'EMPLOYED', 'INVESTOR',
    'FUNDRAISING CONSULTANT', 'INSURANCE AGENT', 'NON-PROFIT VOLUNTEER',
    'BUSINESS',
})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Entity reclassification
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def fix_remaining_misclassified(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Reclassify individuals whose name matches committee patterns
    (e.g. "FRIENDS TO ELECT ...", "...4CONGRESS").

    Returns: (df, n_fixed)
    """
    ii = _indiv_idx(df)
    ln = _norm(df.loc[ii, 'contributor_last_name'])
    cn = _norm(df.loc[ii, 'contributor_name'])

    hits = ii[
        ln.str.contains(_COMMITTEE_IN_NAME_RE.pattern, na=False, regex=True)
        | cn.str.contains(_COMMITTEE_IN_NAME_RE.pattern, na=False, regex=True)
    ]
    n_fixed = len(hits)

    if n_fixed:
        df.loc[hits, 'entity_type'] = 'COMMITTEE/PAC'
        df.loc[hits, 'is_individual'] = False
        df.loc[hits, 'contributor_first_name'] = np.nan
        df.loc[hits, 'contributor_last_name'] = np.nan
        df.loc[hits, 'occupation_category'] = 'POLITICAL COMMITTEE'
        df.loc[hits, 'occupation_status'] = 'NOT_APPLICABLE'
        df.loc[hits, 'contributor_occupation'] = np.nan
        df.loc[hits, 'contributor_employer'] = 'CAMPAIGN/COMMITTEE'

        for idx in hits:
            name = str(df.at[idx, 'contributor_name'])
            matched = False
            for comm_type, pattern in COMM_PATTERNS:
                if pattern.search(name):
                    df.at[idx, 'committee_type'] = comm_type
                    matched = True
                    break
            if not matched:
                df.at[idx, 'committee_type'] = 'POLITICAL COMMITTEE'

    return df, n_fixed


def fix_misclassified_business_entities(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Reclassify individuals that are actually business entities.

    Catches two patterns:
      A) Name contains business suffix (LLC, LLP, INC, CORP, etc.)
      B) Name has no comma + no first name + empty employer → org, not person

    Returns: (df, n_fixed)
    """
    ii = _indiv_idx(df)
    cn = _norm(df.loc[ii, 'contributor_name'])
    fn = df.loc[ii, 'contributor_first_name'].fillna('').astype(str).str.strip()
    emp = df.loc[ii, 'contributor_employer'].fillna('').astype(str).str.strip().str.upper()

    # A) Business suffix in name
    has_suffix = cn.str.contains(_BUSINESS_SUFFIX_RE.pattern, na=False, regex=True)

    # B) No comma + no first name + empty employer
    no_comma = ~cn.str.contains(',', na=False)
    no_first = (fn == '') | (fn.str.upper() == 'NAN')
    empty_emp = emp.isin({'', 'NAN', 'NONE'})
    looks_like_org = no_comma & no_first & empty_emp

    hits = ii[has_suffix | looks_like_org]
    n_fixed = len(hits)

    if n_fixed:
        df.loc[hits, 'entity_type'] = 'COMMITTEE/PAC'
        df.loc[hits, 'is_individual'] = False
        df.loc[hits, 'contributor_first_name'] = np.nan
        df.loc[hits, 'contributor_last_name'] = np.nan
        df.loc[hits, 'occupation_category'] = 'ORGANIZATION'
        df.loc[hits, 'occupation_status'] = 'NOT_APPLICABLE'
        df.loc[hits, 'contributor_occupation'] = np.nan
        df.loc[hits, 'contributor_employer'] = 'CAMPAIGN/COMMITTEE'

    return df, n_fixed


def normalize_business_names(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Strip legal suffixes (LLC, LLP, INC, etc.) from contributor_name
    so 'MILLER BARONDESS, LLP' and 'MILLER BARONDESS' become the same.

    Only applies to non-individual (committee) records.
    Returns: (df, n_fixed)
    """
    mask = df['entity_type'] != 'INDIVIDUAL'
    names = df.loc[mask, 'contributor_name'].fillna('')
    cleaned = names.str.replace(_LEGAL_SUFFIX_RE, '', regex=True).str.strip()
    changed = (cleaned != names) & (cleaned != '')
    n_fixed = changed.sum()
    if n_fixed:
        df.loc[changed[changed].index, 'contributor_name'] = cleaned[changed]
    return df, int(n_fixed)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Name corrections
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def apply_name_corrections(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Apply manual name corrections.
    Returns: (df, n_fixed)
    """
    n_fixed = 0
    for old, new in _NAME_CORRECTIONS.items():
        mask = df['contributor_name'] == old
        n = mask.sum()
        if n:
            df.loc[mask, 'contributor_name'] = new
            n_fixed += n
    return df, n_fixed


def fix_double_apostrophes(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Fix double apostrophes in names: CHALME'' → CHALME'
    Also fixes escaped apostrophes that slipped through FEC data.

    Returns: (df, n_fixed)
    """
    n_fixed = 0
    for col in ['contributor_name', 'contributor_first_name', 'contributor_last_name']:
        if col in df.columns:
            vals = df[col].fillna('')
            has_double = vals.str.contains("''", na=False)
            if has_double.any():
                df.loc[has_double, col] = vals[has_double].str.replace("''", "'", regex=False)
                if col == 'contributor_name':
                    n_fixed += int(has_double.sum())

    return df, n_fixed


def fix_null_last_name(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Fix individuals where contributor_name starts with literal 'NULL, '
    (FEC data artifact — not a real last name).

    EXCEPTION: If the person has a real employer AND occupation, then 'NULL'
    is likely their actual last name (e.g. JAMES NULL, ATTORNEY at KILPATRICK).

    Returns: (df, n_fixed)
    """
    ii = _indiv_idx(df)
    cn = df.loc[ii, 'contributor_name'].fillna('')
    null_mask = ii[cn.str.startswith('NULL, ')]
    n_fixed = 0

    STATUS = {'RETIRED', 'SELF-EMPLOYED', 'NOT EMPLOYED', 'NOT DISCLOSED', '', 'nan', 'NAN'}

    if len(null_mask):
        for idx in null_mask:
            emp = str(df.at[idx, 'contributor_employer'] or '').strip()
            occ = str(df.at[idx, 'contributor_occupation'] or '').strip()

            # If has real employer + occupation → NULL is a real last name
            if emp and emp not in STATUS and occ and occ not in STATUS:
                df.at[idx, 'contributor_last_name'] = 'NULL'
                first = df.at[idx, 'contributor_name'].replace('NULL, ', '', 1).strip()
                df.at[idx, 'contributor_first_name'] = first
                df.at[idx, 'contributor_name'] = f"NULL, {first}"
            else:
                # Data artifact — strip it
                name = df.at[idx, 'contributor_name']
                first = name.replace('NULL, ', '', 1).strip()
                df.at[idx, 'contributor_name'] = first
                df.at[idx, 'contributor_last_name'] = np.nan
                n_fixed += 1

    return df, n_fixed


def normalize_name_periods(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Remove periods from initials and titles in contributor_name.
    'MOSKOWITZ, I.R.' → 'MOSKOWITZ, I R'
    'BELLFORMISSOURI, WESLEY MR.' → 'BELLFORMISSOURI, WESLEY MR'

    Applies to ALL records (individuals and committees).
    Returns: (df, n_fixed)
    """
    names = df['contributor_name'].fillna('')

    # Remove periods after single letters (initials): "J." → "J ", "I.R." → "I R "
    cleaned = names.str.replace(r'\b([A-Z])\.', r'\1 ', regex=True)
    # Remove periods after titles: "MR." → "MR", "JR." → "JR"
    cleaned = cleaned.str.replace(r'\b(MR|MRS|MS|DR|JR|SR|II|III|IV)\.', r'\1', regex=True)
    # Collapse multiple spaces
    cleaned = cleaned.str.replace(r'\s+', ' ', regex=True).str.strip()

    changed = (cleaned != names) & (cleaned != '')
    n_fixed = int(changed.sum())
    if n_fixed:
        df.loc[changed[changed].index, 'contributor_name'] = cleaned[changed]
    return df, n_fixed


# Professional credentials/degrees that occasionally appear wedged between
# the last name and first name in FEC filings, producing rows like:
#   contributor_name      = "BOBROW, M. D, PHILIP."
#   contributor_last_name = "BOBROW, M. D"
#   contributor_first_name = "PHILIP."
# We recognise these patterns and re-split the name into a proper
# LAST, FIRST pair, dropping the credential entirely (the rest of the
# pipeline has no column to store it). Only well-known credentials are
# matched to avoid false positives on real two-letter last names.
_CREDENTIAL_RE = re.compile(
    r'^\s*(?:'
    r'M\s*D'                       # M.D. / M D / MD
    r'|D\s*O'                      # D.O.
    r'|D\s*V\s*M'                  # D.V.M.
    r'|D\s*D\s*S'                  # D.D.S.
    r'|D\s*M\s*D'                  # D.M.D.
    r'|D\s*P\s*M'                  # D.P.M.
    r'|D\s*C'                      # D.C. (chiropractor)
    r'|O\s*D'                      # O.D. (optometrist)
    r'|PH\s*D'                     # Ph.D.
    r'|J\s*D'                      # J.D. (juris doctor)
    r'|M\s*B\s*A'                  # M.B.A.
    r'|R\s*N'                      # R.N.
    r'|R\s*PH'                     # R.Ph.
    r'|PHARM\s*D'                  # Pharm.D.
    r'|C\s*P\s*A'                  # C.P.A.
    r'|ESQ'                        # Esq.
    r'|P\s*E'                      # P.E. (professional engineer)
    r')\s*$',
    re.IGNORECASE,
)


def fix_credential_in_name(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Fix individuals whose name was filed as 'LAST, CREDENTIAL, FIRST'
    instead of the standard 'LAST, FIRST'. The credential (M.D., Ph.D.,
    J.D., Esq., …) gets dropped because there's no column to store it,
    and the LAST/FIRST split is reconstructed from the remaining parts.

    Example FEC raw row:
        contributor_name      : "BOBROW, M D, PHILIP"      (M. D after period stripping)
        contributor_first_name: "PHILIP"
        contributor_last_name : "BOBROW, M D"

    After this fix:
        contributor_name      : "BOBROW, PHILIP"
        contributor_first_name: "PHILIP"
        contributor_last_name : "BOBROW"

    Should run AFTER `normalize_name_periods` so the input has already
    had its periods stripped — the regex matches both "M.D" and "M D".

    Returns: (df, n_fixed)
    """
    ii = _indiv_idx(df)
    if not len(ii):
        return df, 0

    cn = df.loc[ii, 'contributor_name'].fillna('').astype(str)
    # Only consider names with exactly two commas — a third comma means
    # the structure is something else and we should not touch it.
    candidates = ii[cn.str.count(',') == 2]
    if not len(candidates):
        return df, 0

    pattern = re.compile(r'^([^,]+),\s*([^,]+),\s*(.+)$')
    n_fixed = 0

    for idx in candidates:
        name = df.at[idx, 'contributor_name']
        if not isinstance(name, str):
            continue
        m = pattern.match(name)
        if not m:
            continue

        last_part = m.group(1).strip()
        cred_part = m.group(2).strip().rstrip('.').strip()
        first_part = m.group(3).strip()

        if not _CREDENTIAL_RE.match(cred_part):
            continue

        # Strip stray trailing punctuation that FEC sometimes leaves on
        # the first/last fragments themselves.
        first_clean = first_part.rstrip('.').strip()
        last_clean = last_part.rstrip('.').strip()
        if not first_clean or not last_clean:
            continue

        df.at[idx, 'contributor_name'] = f'{last_clean}, {first_clean}'
        df.at[idx, 'contributor_first_name'] = first_clean
        df.at[idx, 'contributor_last_name'] = last_clean
        n_fixed += 1

    return df, n_fixed


def fix_fullname_in_both_fields(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Fix records where full name is copied to both first_name and last_name.
    e.g. fn='STEPHEN YONATY' ln='STEPHEN YONATY' → fn='STEPHEN' ln='YONATY'

    Returns: (df, n_fixed)
    """
    ii = _indiv_idx(df)
    fn = df.loc[ii, 'contributor_first_name'].fillna('').str.strip()
    ln = df.loc[ii, 'contributor_last_name'].fillna('').str.strip()

    # Same multi-word value in both fields
    mask = (fn.str.upper() == ln.str.upper()) & (fn != '') & fn.str.contains(' ')
    hits = ii[mask]
    n_fixed = len(hits)

    if n_fixed:
        for idx in hits:
            full = df.at[idx, 'contributor_first_name'].strip()
            parts = full.split()
            if len(parts) >= 2:
                new_first = parts[0]
                new_last = ' '.join(parts[1:])
                df.at[idx, 'contributor_first_name'] = new_first
                df.at[idx, 'contributor_last_name'] = new_last
                df.at[idx, 'contributor_name'] = f"{new_last}, {new_first}"

    return df, n_fixed


def fix_employer_equals_occupation(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    When employer = occupation and it's a company name (not generic),
    the person likely put employer in both fields. Set occupation based on context.

    Returns: (df, n_fixed)
    """
    ii = _indiv_idx(df)
    emp = _norm(df.loc[ii, 'contributor_employer'])
    occ = _norm(df.loc[ii, 'contributor_occupation'])

    same = ii[(emp == occ) & (emp != '') & ~emp.isin(_GENERIC_EMP_OCC)]
    n_fixed = len(same)

    if n_fixed:
        for idx in same:
            val = df.at[idx, 'contributor_occupation']
            fn = (df.at[idx, 'contributor_first_name'] or '').upper()
            ln = (df.at[idx, 'contributor_last_name'] or '').upper()
            val_upper = (val or '').upper()

            if fn and ln and (fn in val_upper or ln in val_upper):
                # Their own name = self-employed
                df.at[idx, 'contributor_occupation'] = 'SELF-EMPLOYED'
                df.at[idx, 'occupation_category'] = 'SELF-EMPLOYED'
                df.at[idx, 'occupation_status'] = 'DISCLOSED'
            else:
                # Company name in both → clear occupation
                df.at[idx, 'contributor_occupation'] = np.nan
                df.at[idx, 'occupation_category'] = pd.NA
                df.at[idx, 'occupation_status'] = 'MISSING'

    return df, n_fixed


def fix_is_individual_column(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Set is_individual to 'True'/'False' based on entity_type.
    INDIVIDUAL → 'True', COMMITTEE/PAC → 'False'.

    Returns: (df, n_fixed)
    """
    if 'is_individual' not in df.columns:
        df['is_individual'] = pd.Series(dtype='boolean', index=df.index)

    before = df['is_individual'].copy()

    df['is_individual'] = df['entity_type'] == 'INDIVIDUAL'

    n_fixed = int((before.fillna(-1).astype(str) != df['is_individual'].astype(str)).sum())
    return df, n_fixed
