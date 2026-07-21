"""cleaning/safety_nets/employer.py — employer-field safety-net fixes (junk clearing + employer/occupation swaps)."""
from __future__ import annotations

import re
import numpy as np
import pandas as pd
from fec.config.constants import (
    SKIP_EMPLOYERS, SKIP_OCCUPATIONS, REFUSAL_EMPLOYERS, OK_SHORT_EMPLOYERS, OCCUPATION_AS_EMPLOYER, ROLE_AS_EMPLOYER, JOB_TITLE_AS_EMPLOYER, SELF_EMPLOYED_OCC_AS_EMP, SECTOR_AS_EMPLOYER, ADMIN_NOTE_EMPLOYER_RE,
)
from fec.cleaning.occupations import _categorize


def _fix_filled_but_null_employer(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """F. employer_change_type='filled' but employer=NULL → 'cleared'."""
    mask = is_indiv & (df['employer_change_type'] == 'filled') & df['contributor_employer'].isna()
    n = int(mask.sum())
    if n:
        df.loc[mask, 'employer_change_type'] = 'cleared'
    return n


def _clear_refusal_employers(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """M. Employer is a refusal word (PRIVATE, CONFIDENTIAL) — clear it."""
    mask = is_indiv & df['contributor_employer'].fillna('').str.upper().str.strip().isin(REFUSAL_EMPLOYERS)
    n = int(mask.sum())
    if not n:
        return 0

    has_real = mask & (df['occupation_status'] == 'DISCLOSED')
    has_nd = mask & (df['occupation_status'] == 'NOT_DISCLOSED')
    df.loc[has_real, 'contributor_employer'] = pd.NA
    df.loc[has_real, 'employer_name_normalized'] = pd.NA
    df.loc[has_real, 'occupation_status'] = 'EMPLOYER_MISSING'
    df.loc[has_real, 'employer_change_type'] = 'cleared'
    df.loc[has_nd, 'contributor_employer'] = pd.NA
    df.loc[has_nd, 'employer_name_normalized'] = pd.NA
    df.loc[has_nd, 'employer_change_type'] = 'cleared'
    return n


def _clear_admin_note_employers(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """Employer is an FEC admin-note / refusal phrasing ("PER BEST EFFORTS",
    "REQUEST SENT", "DECLINED", "PREFER NOT TO DISCLOSE", "PENDING"...) — not a
    company. Family regex catches new wording the exact-match REFUSAL/RAW_JUNK
    sets miss; full-anchored so a real name CONTAINING such a word is kept."""
    emp = df['contributor_employer'].fillna('').str.upper().str.strip()
    mask = is_indiv & emp.str.match(ADMIN_NOTE_EMPLOYER_RE, na=False)
    return _null_employer_where(df, mask)


def _clear_orphan_normalized(df: pd.DataFrame) -> int:
    """N. Clear employer_name_normalized where contributor_employer is NULL."""
    mask = df['contributor_employer'].isna() & df['employer_name_normalized'].notna()
    n = int(mask.sum())
    if n:
        df.loc[mask, 'employer_name_normalized'] = pd.NA
    return n


def _null_employer_where(df: pd.DataFrame, mask: pd.Series, *, set_status='EMPLOYER_MISSING') -> int:
    """Null contributor_employer where mask is True; clear employer_name_normalized
    and (optionally) set occupation_status. Returns the number of rows changed."""
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = np.nan
        if set_status is not None:
            df.loc[mask, 'occupation_status'] = set_status
        if 'employer_name_normalized' in df.columns:
            df.loc[mask, 'employer_name_normalized'] = pd.NA
    return n


def _swap_occ_emp_fields(df: pd.DataFrame, mask: pd.Series, *, status=None) -> None:
    """Swap contributor_employer <-> contributor_occupation where mask is True,
    optionally setting occupation_status (filer entered the two fields reversed)."""
    old_emp = df.loc[mask, 'contributor_employer'].copy()
    old_occ = df.loc[mask, 'contributor_occupation'].copy()
    df.loc[mask, 'contributor_employer'] = old_occ
    df.loc[mask, 'contributor_occupation'] = old_emp
    if status is not None:
        df.loc[mask, 'occupation_status'] = status


def _null_short_employer_junk(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """O. Null out truncated 2-char employer junk."""
    emp = df['contributor_employer']
    mask = is_indiv & emp.notna() & emp.str.len().le(2) & ~emp.isin(OK_SHORT_EMPLOYERS)
    return _null_employer_where(df, mask, set_status=None)


def _fix_numeric_employer_final(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """Q. Final pass: numeric-only employer (credit card #, ZIP, phone) → NaN.
    Catches values that survived earlier cleaning (e.g. '5189410218248071')."""
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.str.match(r'^\d+$', na=False)
    return _null_employer_where(df, mask)


def _fix_email_employer_final(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """R. Final pass: email in employer → NaN.
    Catches emails that survived earlier cleaning (e.g. 'TKUSHNER@GMAIL.COM')."""
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.str.contains('@', na=False)
    return _null_employer_where(df, mask)


def _fix_junk_employer_patterns(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """S. Junk patterns: XXXXXXXXX, all-same-char, etc. → NaN."""
    emp = df['contributor_employer'].fillna('')
    # All same character repeated (XXXXXXXXX, AAAAAAA, ------)
    all_same = is_indiv & emp.str.match(r'^(.)\1{3,}$', na=False)
    n = int(all_same.sum())
    if n:
        df.loc[all_same, 'contributor_employer'] = np.nan
        df.loc[all_same, 'occupation_status'] = 'NOT_DISCLOSED'
    return n


def _fix_retired_typos(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """T. Common RETIRED typos: TETIRED, RETURED, RETIERD, RETIED, REITRED → RETIRED.
    Also splits 'RETIRED <X>' employers: X routes to previous_employer when it
    is a company, to occupation when it is a profession word."""
    emp = df['contributor_employer'].fillna('')
    _TYPOS = {'TETIRED', 'RETURED', 'RETIERD', 'RETIED', 'REITRED', 'RETIREE',
              'RETIREED', 'RERTIRED', 'RETIRD', 'REIRED', 'REITERED',
              'RETITED'}  # full-column audit 2026-07-21
    mask = is_indiv & emp.isin(_TYPOS)
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = 'RETIRED'
        df.loc[mask, 'contributor_occupation'] = 'RETIRED'
        df.loc[mask, 'occupation_category'] = 'RETIRED'
        df.loc[mask, 'occupation_status'] = 'NOT_APPLICABLE'

    from fec.config.constants import OCCUPATION_AS_EMPLOYER
    embedded = is_indiv & emp.str.match(r'^RETIRED\b[\s,-]+\S', na=False)
    for idx in df.index[embedded]:
        rest = re.sub(r'^RETIRED\b[\s,-]+', '', emp.at[idx]).strip()
        df.at[idx, 'contributor_employer'] = 'RETIRED'
        occ = str(df.at[idx, 'contributor_occupation'] or '')
        if rest in OCCUPATION_AS_EMPLOYER:
            if not occ or occ == 'RETIRED':
                df.at[idx, 'contributor_occupation'] = rest
        elif len(rest) >= 4 and rest not in ('MILITARY',):
            if 'previous_employer' not in df.columns:
                df['previous_employer'] = pd.Series(dtype='object', index=df.index)
            if not str(df.at[idx, 'previous_employer'] or '').strip():
                df.at[idx, 'previous_employer'] = rest
        n += 1
    return n


_COMPANY_NAME_RE = re.compile(
    # Markers that the string is a real legal entity name, NOT an industry word.
    # If the same string appears in emp + occ but matches this, the donor
    # actually works at that company and just put it in both fields; we should
    # NOT convert them to SELF-EMPLOYED. Non-capturing group (?:...) avoids the
    # pandas regex-with-group warning.
    # Also used by _fix_employer_is_occupation_word Case C to detect when the
    # occ field actually holds the real company name (filer swapped fields).
    r'\b(?:LLC|LLP|L\.L\.C\.?|L\.L\.P\.?|INC\.?|CORP\.?|CO\.?|'
    r'LTD\.?|LP|PLC|P\.?C\.?|P\.?A\.?|COMPANY|CORPORATION|'
    r'HOLDINGS|GROUP|PARTNERS|VENTURES|FUND|CAPITAL|'
    r'ASSOCIATES|ENTERPRISES|PROPERTIES|REALTY|ADVISORS|'
    r'INSURANCE|INDUSTRIES|BROTHERS|BANK|FINANCIAL|MEDIA|'
    r'HEALTH|HEALTHCARE|SOLUTIONS|SERVICES|SYSTEMS|TECHNOLOGIES|'
    r'MANAGEMENT|FOUNDATION|UNIVERSITY|COLLEGE|HOSPITAL|CENTER|'
    r'INTERNATIONAL|GLOBAL|TRUST)\b'
    # OR any ampersand between two words (BROWN & BROWN, BAKER & MCKENZIE):
    r'|\b\w+\s*&\s*\w+\b',
    re.IGNORECASE,
)


def _fix_employer_equals_occupation(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """U. Employer == Occupation (same text) → SELF-EMPLOYED.

    e.g. emp='REAL ESTATE' occ='REAL ESTATE' → emp='SELF-EMPLOYED'.

    Excludes real company names: emp='NACHSHON VENTURES LLC' occ='NACHSHON
    VENTURES LLC' is kept as-is (the donor works AT that company and just
    filled the same name into both fields).
    """
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')
    looks_like_company = emp.str.contains(_COMPANY_NAME_RE, na=False)
    mask = (
        is_indiv
        & (emp == occ)
        & emp.ne('')
        & ~emp.isin(SKIP_EMPLOYERS)
        & ~looks_like_company
    )
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = 'SELF-EMPLOYED'
    return n


def _fix_employer_is_occupation_word(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """V. Employer is an occupation word → swap or set SELF-EMPLOYED.
    e.g. emp='PHYSICIAN' occ='RETIRED' → emp='RETIRED' occ='PHYSICIAN'.
         emp='PSYCHOLOGIST' occ=NaN    → emp='SELF-EMPLOYED' occ='PSYCHOLOGIST'."""
    _OCC_WORDS = OCCUPATION_AS_EMPLOYER
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')

    mask = is_indiv & emp.isin(_OCC_WORDS)
    n = int(mask.sum())
    if not n:
        return 0

    # Case A: occ is RETIRED → swap (emp was their old occupation, not employer)
    is_retired = mask & occ.isin({'RETIRED', 'NOT EMPLOYED'})
    if is_retired.any():
        old_emp = df.loc[is_retired, 'contributor_employer'].copy()
        df.loc[is_retired, 'contributor_occupation'] = old_emp
        df.loc[is_retired, 'contributor_employer'] = 'RETIRED'
        df.loc[is_retired, 'occupation_status'] = 'NOT_APPLICABLE'

    # Case B: occ is empty or SELF-EMPLOYED → move occupation word to occupation,
    # set employer to SELF-EMPLOYED
    occ_empty_or_se = mask & ~occ.isin({'RETIRED', 'NOT EMPLOYED'}) & (
        occ.isin({'', 'SELF-EMPLOYED'}) | df['contributor_occupation'].isna()
    )
    if occ_empty_or_se.any():
        old_emp = df.loc[occ_empty_or_se, 'contributor_employer'].copy()
        df.loc[occ_empty_or_se, 'contributor_occupation'] = old_emp
        df.loc[occ_empty_or_se, 'occupation_category'] = _categorize(old_emp)
        df.loc[occ_empty_or_se, 'contributor_employer'] = 'SELF-EMPLOYED'
        df.loc[occ_empty_or_se, 'occupation_status'] = 'DISCLOSED'

    # Case C: occ has a different real value.
    # If occ looks like a real company name the filer swapped the fields —
    # SWAP them back rather than setting SELF-EMPLOYED (which would destroy
    # the company name, e.g. emp='CLAIMS' occ='ARCH INSURANCE').
    other = mask & ~is_retired & ~occ_empty_or_se
    if other.any():
        occ_looks_like_company = occ.str.contains(_COMPANY_NAME_RE, na=False)
        # Case C-1: occ has a real company name → swap fields (filer swapped them)
        c1 = other & occ_looks_like_company
        if c1.any():
            _swap_occ_emp_fields(df, c1, status='DISCLOSED')
        # Case C-2: occ is a different occupation word → genuinely self-employed
        c2 = other & ~occ_looks_like_company
        if c2.any():
            df.loc[c2, 'contributor_employer'] = 'SELF-EMPLOYED'

    return n


def _fix_role_as_employer(df: pd.DataFrame) -> int:
    """AD. Role/title in employer field — e.g. emp='OWNER' occ='REAL ESTATE'.

    If occ looks like a real company the filer swapped the fields, so SWAP
    them back (e.g. emp='OWNER' occ='TALCOTT HOLDINGS'). Otherwise the
    person is genuinely self-employed → emp='SELF-EMPLOYED'."""
    _ROLES = ROLE_AS_EMPLOYER
    _SKIP_OCC = SKIP_OCCUPATIONS | {'OWNER', 'CEO', 'PRESIDENT'}

    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')

    mask = is_indiv & emp.isin(_ROLES) & ~occ.isin(_SKIP_OCC) & occ.ne('')
    n = int(mask.sum())
    if not n:
        return 0

    occ_looks_like_company = occ.str.contains(_COMPANY_NAME_RE, na=False)
    # Case A: occ is a real company name → swap (filer swapped fields)
    swap = mask & occ_looks_like_company
    if swap.any():
        _swap_occ_emp_fields(df, swap, status='DISCLOSED')

    # Case B: occ is just another occupation → person is genuinely self-employed
    se = mask & ~occ_looks_like_company
    if se.any():
        df.loc[se, 'contributor_employer'] = 'SELF-EMPLOYED'
    return n


def _null_sector_as_employer(df: pd.DataFrame) -> int:
    """AD2. Industry/sector word in the employer field → NULL it.

    "HEALTHCARE", "REAL ESTATE", "FINANCE" name an industry, not a company,
    and don't imply self-employment (unlike OWNER/INVESTOR). Best UX is an
    employer field that holds a real company or nothing, so these are nulled
    while the occupation is kept. We deliberately do NOT try to swap a
    company out of the occupation field: the company detector is unreliable
    on bare names (misses "GOLDMAN SACHS", false-hits "HEALTH TECH") and the
    swap case doesn't occur in the data — nulling is the safe, predictable
    outcome."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.str.upper().isin(SECTOR_AS_EMPLOYER)
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = np.nan
    return n


def _fix_self_employed_consistency(df: pd.DataFrame) -> int:
    """AE. Fix self-employed consistency issues.

    Handles three patterns for individuals with occ='SELF-EMPLOYED':

    1) Employer is the person's own name → set employer=SELF-EMPLOYED
       e.g. name='DRELICH, OREN' emp='OREN DRELICH' → emp='SELF-EMPLOYED'

    2) Employer is actually an occupation word → move to occupation,
       set employer=SELF-EMPLOYED
       e.g. emp='DESIGNER' occ='SELF-EMPLOYED' → emp='SELF-EMPLOYED' occ='DESIGNER'

    3) employer_status='active' but occupation indicates self-employed
       → set employer_status='self_employed'
    """
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')
    n = 0

    # Target: individuals with occ=SELF-EMPLOYED and a non-status employer
    _SKIP_EMP = SKIP_EMPLOYERS
    se_with_company = is_indiv & (occ == 'SELF-EMPLOYED') & ~emp.isin(_SKIP_EMP)

    if se_with_company.any():
        # ── Pattern 1: employer is person's own name ──
        fn = df['contributor_first_name'].fillna('').str.upper()
        ln = df['contributor_last_name'].fillna('').str.upper()
        emp_upper = emp.str.upper()

        for idx in df.loc[se_with_company].index:
            f = fn.at[idx].strip()
            l = ln.at[idx].strip()
            e = emp_upper.at[idx].strip()
            if not f or not l or not e:
                continue
            e_parts = set(e.replace(',', ' ').split())
            name_parts = {f, l}
            if name_parts.issubset(e_parts):
                df.at[idx, 'contributor_employer'] = 'SELF-EMPLOYED'
                n += 1

        # Refresh emp after pattern 1 changes
        emp = df['contributor_employer'].fillna('')
        se_with_company = is_indiv & (occ == 'SELF-EMPLOYED') & ~emp.isin(_SKIP_EMP)

        # ── Pattern 2: employer is an occupation word ──
        _OCC_AS_EMP = SELF_EMPLOYED_OCC_AS_EMP
        emp_is_occ = se_with_company & emp.isin(_OCC_AS_EMP)
        if emp_is_occ.any():
            old_emp = df.loc[emp_is_occ, 'contributor_employer'].copy()
            df.loc[emp_is_occ, 'contributor_occupation'] = old_emp
            df.loc[emp_is_occ, 'occupation_category'] = _categorize(old_emp)
            df.loc[emp_is_occ, 'contributor_employer'] = 'SELF-EMPLOYED'
            df.loc[emp_is_occ, 'occupation_status'] = 'DISCLOSED'
            n += int(emp_is_occ.sum())

    # ── Pattern 3: employer_status consistency ──
    if 'employer_status' in df.columns:
        emp = df['contributor_employer'].fillna('')
        se_active = (
            is_indiv
            & (df['employer_status'] == 'active')
            & (
                (df['occupation_category'] == 'SELF-EMPLOYED')
                | (occ == 'SELF-EMPLOYED')
                | (emp == 'SELF-EMPLOYED')
            )
        )
        n_status = int(se_active.sum())
        if n_status:
            df.loc[se_active, 'employer_status'] = 'self_employed'
            n += n_status

    return n


def _name_word_set(*parts: str) -> frozenset:
    """The comparable word-set of a personal name.

    Punctuation is dropped and single letters are ignored, so "HOWARD, ALIDA",
    "ALIDA HOWARD" and "ALIDA B HOWARD" all reduce to {ALIDA, HOWARD}. Order is
    NOT preserved on purpose: the FEC stores names "LAST, FIRST" while filers
    write the employer field "FIRST LAST"."""
    words = re.sub(r'[^A-Z]', ' ', ' '.join(parts).upper()).split()
    return frozenset(w for w in words if len(w) > 1)


def _fix_own_name_as_employer(df: pd.DataFrame) -> int:
    """AE2. Employer field holds the donor's OWN name → SELF-EMPLOYED.

    Someone who works for themselves often writes their own name in the
    employer field ("HOWARD, ALIDA" → employer "ALIDA HOWARD"), which then
    becomes a phantom one-person company in employers.csv. AE's pattern 1
    already covers this, but only fires when occ='SELF-EMPLOYED'; in practice
    these rows carry a real occupation (RETIRED, PHYSICIAN, CONSULTANT…), so
    they slip through.

    Requires the WHOLE name — both given and family name. A shared surname
    alone is normal and correct: a Richard Comiter really may work at
    "COMITER, SINGER, BASEMAN & BRAUN", and "BROWNSTEIN" is a law firm as
    often as it is a donor."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.ne('') & ~emp.isin(SKIP_EMPLOYERS)
    if not mask.any():
        return 0

    first = df['contributor_first_name'].fillna('')
    last = df['contributor_last_name'].fillna('')
    has_mid = 'contributor_middle_name' in df.columns
    mid = df['contributor_middle_name'].fillna('') if has_mid else None

    hits = []
    for idx in df.index[mask]:
        f, l = _name_word_set(first.at[idx]), _name_word_set(last.at[idx])
        if not f or not l:
            continue  # need the full name to be safe
        own = {f | l}
        if has_mid:
            m = _name_word_set(mid.at[idx])
            if m:
                own.add(f | m | l)
        if _name_word_set(emp.at[idx]) in own:
            hits.append(idx)

    if hits:
        df.loc[hits, 'contributor_employer'] = 'SELF-EMPLOYED'
        if 'employer_name_normalized' in df.columns:
            df.loc[hits, 'employer_name_normalized'] = pd.NA
    return len(hits)


def _fix_truncated_employer_38(df: pd.DataFrame) -> int:
    """AI. Employer names truncated at 38 chars → merge with full version.
    FEC data truncates employer at 38 chars, creating duplicates like
    'ASSOCIATES IN GASTROENTEROLOGY OF UC' vs '...OF UNION COUNTY'."""
    emp_col = df['contributor_employer']
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    indiv_emp = emp_col[is_indiv].dropna()

    # Find all employers with exactly 38 chars
    trunc_candidates = set(indiv_emp[indiv_emp.str.len() == 38].unique())
    if not trunc_candidates:
        return 0

    # Build lookup: all employers longer than 38 chars
    all_emps = set(indiv_emp.unique())
    long_emps = {e for e in all_emps if isinstance(e, str) and len(e) > 38}

    # Match: truncated version is prefix of a longer version
    trunc_to_full = {}
    counts = indiv_emp.value_counts()
    for trunc in trunc_candidates:
        matches = [full for full in long_emps if full.startswith(trunc)]
        if len(matches) == 1:
            trunc_to_full[trunc] = matches[0]
        elif len(matches) > 1:
            # Pick the most common one
            best = max(matches, key=lambda m: counts.get(m, 0))
            trunc_to_full[trunc] = best

    if not trunc_to_full:
        return 0

    mask = is_indiv & emp_col.isin(trunc_to_full.keys())
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = emp_col[mask].map(trunc_to_full)
        # Preserve original if not already set
        if 'contributor_employer_original' in df.columns:
            orig_null = mask & df['contributor_employer_original'].isna()
            df.loc[orig_null, 'contributor_employer_original'] = emp_col[orig_null]
    return n


def _fix_company_name_as_occupation(df: pd.DataFrame) -> int:
    """AK. Company name in occupation field with SELF-EMPLOYED employer.
    e.g. emp='SELF-EMPLOYED' occ='STERLINGRISK' -> swap (occ is the real employer).
    Only when occ value also appears frequently as an employer in the dataset."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')

    # Only look at SELF-EMPLOYED individuals with a non-empty occupation
    se_mask = is_indiv & (emp == 'SELF-EMPLOYED') & (occ != '')

    # Build set of values that appear as employers (10+ times)
    emp_counts = df.loc[is_indiv, 'contributor_employer'].value_counts()
    known_employers = set(emp_counts[emp_counts >= 10].index)

    # Find cases where occ is a known employer name
    occ_is_company = se_mask & occ.isin(known_employers)
    n = int(occ_is_company.sum())
    if not n:
        return 0

    # Swap: move company from occ to emp, set occ from what other employees have
    for occ_val in df.loc[occ_is_company, 'contributor_occupation'].unique():
        other_emps = df[(df['contributor_employer'] == occ_val) & is_indiv]
        real_occs = other_emps['contributor_occupation'].dropna()
        real_occs = real_occs[~real_occs.isin({'RETIRED', 'SELF-EMPLOYED', 'NOT EMPLOYED', ''})]
        best_occ = real_occs.value_counts().index[0] if len(real_occs) > 0 else None

        mask = occ_is_company & (occ == occ_val)
        df.loc[mask, 'contributor_employer'] = occ_val
        if best_occ:
            df.loc[mask, 'contributor_occupation'] = best_occ
        df.loc[mask, 'occupation_status'] = 'DISCLOSED'

    return n


def _fix_swapped_emp_occ_company(df: pd.DataFrame) -> int:
    """AL. emp=job title, occ=company name -> swap them.
    e.g. emp='REAL ESTATE' occ='HOWARD PROPERTIES' -> swap."""
    _TITLES_AS_EMP = JOB_TITLE_AS_EMPLOYER
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('').str.upper()
    occ = df['contributor_occupation'].fillna('')

    emp_is_title = is_indiv & emp.isin(_TITLES_AS_EMP)
    occ_has_company = occ.str.contains(
        r'\b(?:LLC|INC|CORP|GROUP|PARTNERS|CAPITAL|REALTY|PROPERTIES|ADVISORS|HOLDINGS)\b',
        na=False, case=False)
    mask = emp_is_title & occ_has_company
    n = int(mask.sum())
    if not n:
        return 0

    _swap_occ_emp_fields(df, mask)
    return n


def _fix_occ_emp_both_swapped(df: pd.DataFrame) -> int:
    """AO. Both occ and emp filled but swapped (occ=company, emp=job title).
    e.g. occ='ARCH INSURANCE' emp='CLAIMS' -> swap.
    Also: occ='BROWN & BROWN' emp='INSURANCE BROKER' -> swap."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')

    # Known swapped pairs: occ is a company name, emp is a job description
    _OCC_IS_COMPANY = {
        'ARCH INSURANCE', 'BROWN & BROWN', 'DANZIGER & DE LLANO LLP',
        'NNA SERVICES LLC', 'BAKER TILLY', 'AMERICAN EXPRESS',
        'NFI INDUSTRIES', 'SKYRISE PROPERTIES', 'PAYROLL COMPANY',
    }
    mask = is_indiv & occ.isin(_OCC_IS_COMPANY) & (emp != '')
    n = int(mask.sum())
    if n:
        _swap_occ_emp_fields(df, mask)
    return n


def _swap_role_employer_with_known_company(df: pd.DataFrame) -> int:
    """AD0. Employer holds a job title while the OCCUPATION holds the company.

    Runs BEFORE _fix_role_as_employer (AD), which is the whole point. AD turns a
    bare title into SELF-EMPLOYED, and its swap-back branch only fires when
    _COMPANY_NAME_RE recognises the occupation — a detector that misses bare
    brand names ('KIMCO', 'MERRILL LYNCH', 'JINSA'). So a filer who swapped the
    two fields lost their real employer to SELF-EMPLOYED, and the company was
    left stranded in the occupation field. AD0 catches that case first.

    The evidence is the dataset itself: the occupation value is only treated as
    a company when OTHER donors already use that exact string as their employer.
    That is a fact in the data, not a guess about the string's shape, so it
    needs no company-name heuristic and it keeps working as new filings arrive.
    """
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('').astype(str).str.strip().str.upper()
    occ = df['contributor_occupation'].fillna('').astype(str).str.strip().str.upper()

    titles = ROLE_AS_EMPLOYER | OCCUPATION_AS_EMPLOYER | JOB_TITLE_AS_EMPLOYER
    candidates = is_indiv & emp.isin(titles) & occ.ne('')
    if not candidates.any():
        return 0

    # Employers other donors actually use — the rows we are about to fix are
    # excluded, so a title can never vouch for itself.
    real = set(emp[is_indiv & ~emp.isin(titles) & ~emp.isin(SKIP_EMPLOYERS) & emp.ne('')])
    if not real:
        return 0

    mask = candidates & occ.isin(real)
    n = int(mask.sum())
    if n:
        _swap_occ_emp_fields(df, mask, status='DISCLOSED')
        if 'employer_name_normalized' in df.columns:
            df.loc[mask, 'employer_name_normalized'] = pd.NA
    return n
