"""Classify contributors and apply entity and name corrections."""
from __future__ import annotations

from pathlib import Path
import pandas as pd
from fec.donor_match.rules import held_groups
from fec.env import FEC_SOURCE_CSV
import re
import numpy as np
from fec.cleaning._helpers import _norm, _indiv_idx
from fec.cleaning.names import EXACT_NAME_CORRECTIONS, ROW_NAME_CORRECTIONS
from fec.config.constants import STATUS_CATEGORIES
from fec.config.geography import US_STATES
from fec.config.not_employers import LEGAL_SUFFIX_RE
from fec.config.data import INDIV_NAME_RE, ORG_KEYWORDS
from fec.cleaning.work.normalize import (
    EMPLOYER_STATUS_TEXT,
    _categorize,
    _normalize_text,
    map_occupation_fixes,
)
from fec.cleaning.work.employer_aliases import (
    documented_employer_name, employer_rule_key, reviewed_employer_name,
)
from fec.cleaning.work.status import is_job_title, is_real_employer
from fec.config.constants import OK_SHORT_EMPLOYERS, OK_SHORT_OCCUPATIONS
from fec.config.data import MISSING_VALUES
from fec.config.employers import EMPLOYER_NORMALIZE
from fec.config.occupation_rules.normalize import OCCUPATION_NORMALIZE
from fec.config.occupation_rules.rules import SWAP_JOB_TITLES


SOURCE_COLUMNS = ("fec_entity_type", "fec_contributor_id", "fec_image_number")

# FEC entity_type codes: a candidate (CAN) giving personally is a person
FEC_ENTITY_TYPES = {
    "IND": "INDIVIDUAL",
    "CAN": "INDIVIDUAL",
    "ORG": "ORGANIZATION",
    "COM": "COMMITTEE/PAC",
    "PAC": "COMMITTEE/PAC",
    "PTY": "COMMITTEE/PAC",
    "CCM": "COMMITTEE/PAC",
}


def attach_source_fields(df: pd.DataFrame, path: Path = FEC_SOURCE_CSV) -> None:
    """Attach FEC source fields by sub_id; leave missing values blank."""
    source = (
        pd.read_csv(path, dtype=str, keep_default_na=False).drop_duplicates("sub_id", keep="last").set_index("sub_id")
        if Path(path).exists() else pd.DataFrame(columns=list(SOURCE_COLUMNS))
    )
    sub_ids = df["sub_id"].astype(str).str.strip()
    for column in SOURCE_COLUMNS:
        values = source[column] if column in source.columns else pd.Series(dtype=object)
        df[column] = sub_ids.map(values).fillna("").astype(str).str.strip().str.upper().to_numpy()


def source_entity_type(df: pd.DataFrame) -> pd.Series:
    """Map FEC codes to entity types, leaving unknown codes blank."""
    if "fec_entity_type" not in df.columns:
        return pd.Series("", index=df.index, dtype=object)
    codes = df["fec_entity_type"].astype("string").fillna("").str.strip().str.upper()
    return codes.map(FEC_ENTITY_TYPES).fillna("").astype(object)


def deciding_source_type(df: pd.DataFrame) -> pd.Series:
    """Use FEC's type only for confirmed filings."""
    source = source_entity_type(df)
    if not source.ne("").any():
        return source
    undecided = held_groups(df) != ""
    if "identity_status" in df.columns:
        undecided |= df["identity_status"].fillna("confirmed").ne("confirmed")
    return source.where(~undecided, "")


# political-committee wording in a name: "FRIENDS OF SMITH", "SMITH FOR SENATE"
_COMMITTEE_IN_NAME_RE = re.compile(
    r'(?:FRIENDS|CITIZENS|COMMITTEE) TO ELECT'
    r'|(?:FRIENDS OF|FRIENDS FOR|CITIZENS FOR|PEOPLE FOR) '
    r'|FOR (?:CONGRESS|SENATE|U\.?S\.?\s*SENATE|AMERICA|GOVERNOR|MAYOR)'
    r'|FOR (?:NC|NEVADA|TEXAS|VIRGINIA|COLORADO|NEW YORK|MICHIGAN|OHIO|CALIFORNIA'
    r'|GEORGIA|FLORIDA|ARIZONA|PENNSYLVANIA|WISCONSIN|MISSOURI|INDIANA|MARYLAND'
    r'|MINNESOTA|WASHINGTON|ILLINOIS|OKLAHOMA)\b'
    r'|(?:VICTORY|ACTION|LEADERSHIP) FUND|LEADERSHIP PAC'
    r'|BELLFORMISSOURI'
    r'|4\s*CONGRESS|4\s*UTAH|4\s*SENATE'
    r'|CAMPAIGN$'
    r'|\.COM$'
    r'|ELECT\w+\.COM',
    re.IGNORECASE,
)

# a business legal suffix: "LLC", "INC", "P.C.", "P.A."
_BUSINESS_SUFFIX_RE = re.compile(
    r'\b(?:LLC|LLP|L\.L\.C|L\.L\.P|INC|CORP|LTD|P\.?L\.?L\.?C)\b'
    r'|\bP\.?C\.?\b|\bP\.?A\.?\b',
    re.IGNORECASE
)

_GENERIC_EMP_OCC = STATUS_CATEGORIES | frozenset({
    'NOT DISCLOSED', 'REAL ESTATE', 'FINANCE', 'SALES', 'CONSULTING', 'MANAGEMENT',
    'INSURANCE', 'MARKETING', 'BANKING', 'GOVERNMENT', 'EDUCATION',
    'CONSTRUCTION', 'ACCOUNTING', 'TECHNOLOGY', 'ENGINEERING', 'VOLUNTEER',
    'CASHIER', 'NURSE', 'HEALTHCARE', 'ENTREPRENEUR', 'INDEPENDENT CONTRACTOR',
    'FREELANCE', 'COMMUNITY VOLUNTEER', 'EMPLOYED', 'INVESTOR',
    'FUNDRAISING CONSULTANT', 'INSURANCE AGENT', 'NON-PROFIT VOLUNTEER',
    'BUSINESS',
})

# Known credentials only, so real two-letter last names are not mistaken for one.
_CREDENTIAL_RE = re.compile(
    r'^\s*(?:'
    r'M\s*D'
    r'|D\s*O'
    r'|D\s*V\s*M'
    r'|D\s*D\s*S'
    r'|D\s*M\s*D'
    r'|D\s*P\s*M'
    r'|D\s*C'
    r'|O\s*D'
    r'|PH\s*D'
    r'|J\s*D'
    r'|M\s*B\s*A'
    r'|R\s*N'
    r'|R\s*PH'
    r'|PHARM\s*D'
    r'|C\s*P\s*A'
    r'|ESQ'
    r'|P\s*E'
    r')\s*$',
    re.IGNORECASE,
)

# 'LAST, CREDENTIAL, FIRST'
_THREE_PART_NAME_RE = re.compile(r'^([^,]+),\s*([^,]+),\s*(.+)$')


def _mark_as_committee(df: pd.DataFrame, hits, category: str) -> None:
    """Set the committee type and clear individual fields."""
    df.loc[hits, 'entity_type'] = 'COMMITTEE/PAC'
    df.loc[hits, 'is_individual'] = False
    df.loc[hits, 'contributor_first_name'] = np.nan
    df.loc[hits, 'contributor_last_name'] = np.nan
    df.loc[hits, 'occupation_category'] = category
    df.loc[hits, 'occupation_status'] = 'NOT_APPLICABLE'
    df.loc[hits, 'contributor_occupation'] = np.nan
    df.loc[hits, 'contributor_employer'] = np.nan


def fix_remaining_misclassified(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Reclassify individuals whose name matches committee patterns as COMMITTEE/PAC."""
    indiv_idx = _indiv_idx(df)
    last_names = _norm(df.loc[indiv_idx, 'contributor_last_name'])
    names = _norm(df.loc[indiv_idx, 'contributor_name'])

    hits = indiv_idx[
        last_names.str.contains(_COMMITTEE_IN_NAME_RE.pattern, na=False, regex=True)
        | names.str.contains(_COMMITTEE_IN_NAME_RE.pattern, na=False, regex=True)
    ]
    n_fixed = len(hits)

    if n_fixed:
        _mark_as_committee(df, hits, 'POLITICAL COMMITTEE')

    return df, n_fixed


def fix_misclassified_business_entities(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Reclassify individuals that are actually business entities."""
    indiv_idx = _indiv_idx(df)
    names = _norm(df.loc[indiv_idx, 'contributor_name'])
    first_names = df.loc[indiv_idx, 'contributor_first_name'].fillna('').astype(str).str.strip()
    emp = df.loc[indiv_idx, 'contributor_employer'].fillna('').astype(str).str.strip().str.upper()

    has_suffix = names.str.contains(_BUSINESS_SUFFIX_RE.pattern, na=False, regex=True)

    no_comma = ~names.str.contains(',', na=False, regex=False)
    no_first = (first_names == '') | (first_names.str.upper() == 'NAN')
    empty_emp = emp.isin({'', 'NAN', 'NONE'})
    looks_like_org = no_comma & no_first & empty_emp

    hits = indiv_idx[has_suffix | looks_like_org]
    n_fixed = len(hits)

    if n_fixed:
        _mark_as_committee(df, hits, 'ORGANIZATION')
    return df, n_fixed


# Keep campaign phrases such as ROB FOR PA intact.
_FOR_STATE_RE = re.compile(r'\bFOR (?:' + '|'.join(sorted(US_STATES)) + r')$')


def normalize_business_names(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Retain company legal forms and the existing committee-name format."""
    from fec.cleaning.donors.entity import read_entity_overrides

    names = df.loc[df['entity_type'].ne('INDIVIDUAL'), 'contributor_name'].fillna('')
    types = df.loc[names.index, 'entity_type']
    cleaned = names.copy()
    company_names = set(names[types.eq('ORGANIZATION')].map(employer_rule_key))
    company_names.update(employer_rule_key(name) for name, kind in read_entity_overrides().items()
                         if kind == 'ORGANIZATION')
    organizations = names.map(employer_rule_key).isin(company_names)
    cleaned.loc[organizations] = names[organizations].map(reviewed_employer_name)
    committees = types.eq('COMMITTEE/PAC') & ~organizations & ~names.str.contains(_FOR_STATE_RE)
    cleaned.loc[committees] = names[committees].str.replace(LEGAL_SUFFIX_RE, '', regex=True).str.strip()
    # A documented company alias can also correct a guessed committee name.
    cleaned = names.map(documented_employer_name).fillna(cleaned)
    changed = (cleaned != names) & (cleaned != '')
    n_fixed = changed.sum()
    if n_fixed:
        df.loc[changed[changed].index, 'contributor_name'] = cleaned[changed]
    return df, int(n_fixed)


def fix_employer_equals_occupation(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Keep a duplicated job in occupation; keep a duplicated company in employer."""
    indiv_idx = _indiv_idx(df)
    emp = _norm(df.loc[indiv_idx, 'contributor_employer'])
    occ = _norm(df.loc[indiv_idx, 'contributor_occupation'])

    same = indiv_idx[
        emp.map(employer_rule_key).eq(occ.map(employer_rule_key))
        & emp.ne('') & ~emp.isin(_GENERIC_EMP_OCC)
    ]
    n_fixed = len(same)

    jobs = same[occ.loc[same].map(is_job_title)]
    df.loc[jobs, 'contributor_employer'] = np.nan
    df.loc[jobs, 'occupation_status'] = 'EMPLOYER_MISSING'

    for idx in same.difference(jobs):
        val = df.at[idx, 'contributor_occupation']
        first_name = (df.at[idx, 'contributor_first_name'] or '').upper()
        last_name = (df.at[idx, 'contributor_last_name'] or '').upper()
        val_upper = (val or '').upper()

        if first_name and last_name and (first_name in val_upper or last_name in val_upper):
            # Their own name in both fields: self-employed.
            df.at[idx, 'contributor_occupation'] = 'SELF-EMPLOYED'
            df.at[idx, 'occupation_category'] = 'SELF-EMPLOYED'
            df.at[idx, 'occupation_status'] = 'DISCLOSED'
        else:
            # A company in both fields: occupation unknown.
            df.at[idx, 'contributor_occupation'] = np.nan
            df.at[idx, 'occupation_category'] = pd.NA
            df.at[idx, 'occupation_status'] = 'MISSING'

    return df, n_fixed


def apply_name_corrections(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Apply manual name corrections."""
    corrected = df['contributor_name'].map(EXACT_NAME_CORRECTIONS)
    if 'sub_id' in df.columns:
        by_row = df['sub_id'].astype(str).map(ROW_NAME_CORRECTIONS)
        corrected = by_row.combine_first(corrected)

    changed = corrected.notna() & corrected.ne(df['contributor_name'])
    for index, new_name in corrected[changed].items():
        df.at[index, 'contributor_name'] = new_name
        if df.at[index, 'entity_type'] == 'INDIVIDUAL' and ',' in new_name:
            last, first = (part.strip() for part in new_name.split(',', 1))
            df.at[index, 'contributor_first_name'] = first
            df.at[index, 'contributor_last_name'] = last

    return df, int(changed.sum())


def fix_double_apostrophes(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Collapse doubled apostrophes in name fields (CHALME'' becomes CHALME')."""
    n_fixed = 0
    for col in ['contributor_name', 'contributor_first_name', 'contributor_last_name']:
        if col in df.columns:
            vals = df[col].fillna('')
            has_double = vals.str.contains("''", na=False, regex=False)
            if has_double.any():
                df.loc[has_double, col] = vals[has_double].str.replace("''", "'", regex=False)
                if col == 'contributor_name':
                    n_fixed += int(has_double.sum())

    return df, n_fixed


def normalize_name_periods(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Remove periods from initials and titles in contributor_name, all records."""
    names = df['contributor_name'].fillna('')

    cleaned = names.str.replace(r'\b([A-Z])\.', r'\1 ', regex=True)
    cleaned = cleaned.str.replace(r'\b(MR|MRS|MS|DR|JR|SR|II|III|IV)\.', r'\1', regex=True)
    cleaned = cleaned.str.replace(r'\s+', ' ', regex=True).str.strip()

    changed = (cleaned != names) & (cleaned != '')
    n_fixed = int(changed.sum())
    if n_fixed:
        df.loc[changed[changed].index, 'contributor_name'] = cleaned[changed]
    return df, n_fixed


def fix_credential_in_name(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Remove embedded credentials after normalize_name_periods."""
    indiv_idx = _indiv_idx(df)
    if not len(indiv_idx):
        return df, 0

    names = df.loc[indiv_idx, 'contributor_name'].fillna('').astype(str)
    # Only LAST, CREDENTIAL, FIRST.
    candidates = indiv_idx[names.str.count(',') == 2]
    if not len(candidates):
        return df, 0

    n_fixed = 0

    for idx in candidates:
        name = df.at[idx, 'contributor_name']
        if not isinstance(name, str):
            continue
        match = _THREE_PART_NAME_RE.match(name)
        if not match:
            continue

        credential_part = match.group(2).strip().rstrip('.').strip()

        if not _CREDENTIAL_RE.match(credential_part):
            continue

        first_clean = match.group(3).strip().rstrip('.').strip()
        last_clean = match.group(1).strip().rstrip('.').strip()
        if not first_clean or not last_clean:
            continue

        df.at[idx, 'contributor_name'] = f'{last_clean}, {first_clean}'
        df.at[idx, 'contributor_first_name'] = first_clean
        df.at[idx, 'contributor_last_name'] = last_clean
        n_fixed += 1

    return df, n_fixed


def fix_fullname_in_both_fields(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Split a full name duplicated into both first_name and last_name."""
    indiv_idx = _indiv_idx(df)
    first_names = df.loc[indiv_idx, 'contributor_first_name'].fillna('').str.strip()
    last_names = df.loc[indiv_idx, 'contributor_last_name'].fillna('').str.strip()

    mask = (
        (first_names.str.upper() == last_names.str.upper())
        & (first_names != '')
        & first_names.str.contains(' ', regex=False)
    )
    hits = indiv_idx[mask]
    n_fixed = len(hits)

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


# Business names; committee signals take priority.
_ORG_BUSINESS_RE = re.compile(
    r"\b(?:LLC|INC|CORP|CORPORATION|LLP|LP|HOLDINGS?|ENTERPRISES?"
    r"|VENTURES?|PROPERTIES|PROPERTY|REALTY|REAL ESTATE|MANAGEMENT|PARTNERS"
    r"|PARTNERSHIP|CAPITAL|GROUP|ASSOCIATES|COMPANY|INDUSTRIES|INVESTMENTS?"
    r"|BANK|INSURANCE|FOUNDATION|TRUST|MEDIA|DEVELOPMENT|LENDING|ACQUISITIONS?"
    r"|EQUITIES|TELECOM\w*|CONGREGATION|UNION|COUNCIL|BROTHERS|STEEL"
    r"|CONSTRUCTION|CONSULTANTS?)\b",
    re.IGNORECASE,
)

# Committee names and campaign phrases.
_COMMITTEE_SIGNAL_RE = re.compile(
    r"\b(?:PAC|COMMITTEE|VICTORY|FRIENDS|CITIZENS|FUND|DEMOCRAT"
    r"|REPUBLICAN|SENATE|CONGRESS|ACTION|CAMPAIGN"
    r"|NRSC|NRCC|DCCC|DSCC)\b|FOR (?:CONGRESS|SENATE|AMERICA|GOVERNOR|MAYOR)",
    re.IGNORECASE,
)

# Earlier labels protect committee names that also contain business words.
_POLITICAL_COMMITTEE_TYPES = frozenset(
    {
        "CONGRESSIONAL CAMPAIGN",
        "SENATE CAMPAIGN",
        "POLITICAL COMMITTEE",
        "POLITICAL ACTION COMMITTEE",
        "PARTY ORGANIZATION",
    }
)

# These business names can override a committee label without a committee signal.
_STRONG_BUSINESS_RE = re.compile(
    r"\b(?:BANK|TRUST|INVESTMENTS?|CAPITAL|INSURANCE|REALTY|REAL ESTATE"
    r"|HOLDINGS?|EQUITIES|CORPORATION|ENTERPRISES?"
    r"|FAMILY OFFICE|FAMILY TRUST|FAMILY (?:LIMITED )?PARTNERSHIP)\b",
    re.IGNORECASE,
)

# Committee prefixes, including the joined name BELLFORMISSOURI.
_DEFINITE_COMM_RE = re.compile(
    r"^(?:FRIENDS\s+(?:OF|FOR|TO\s+ELECT)\b"
    r"|BELLFORMISSOURI"
    r"|PEOPLE\s+FOR\b"
    r"|CITIZENS\s+FOR\b"
    r"|TEXANS\s+FOR\b"
    r"|NEVADANS\s+FOR\b"
    r"|ALASKANS\s+FOR\b"
    r"|MONTANANS\s+FOR\b"
    r"|KANSANS\s+FOR\b)",
    re.IGNORECASE,
)

# Organization names on individuals with no first or last name.
_NAMELESS_ORG_RE = re.compile(
    r" (?:FOR |PAC|LLC|LP\b|INC|COMMITTEE|COUNCIL|PARTNERS"
    r"|HOLDINGS|INVESTMENT|COMPANY|SERVICES|MEDIA|PROPERTY"
    r"|TRADES|VENTURES|CORPORATION|GROUP|ASSOCIATION)"
    r"|^(?:DEMOCRATIC|REPUBLICAN) ",
    re.IGNORECASE,
)


def _blank(s: pd.Series) -> pd.Series:
    """True where the value is missing or whitespace-only."""
    return _norm(s) == ""


def _reclassify_entities(df: pd.DataFrame) -> tuple[int, int]:
    """Correct entity types; return person and committee reclassification counts."""
    initial_names = df["contributor_name"].astype(str)
    has_org_keywords = initial_names.str.contains(ORG_KEYWORDS, na=False)
    looks_like_person = initial_names.str.match(INDIV_NAME_RE, na=False)

    df["_reclass_reason"] = pd.Series(pd.NA, index=df.index, dtype="object")

    to_individual = ~df["is_individual"] & ~has_org_keywords & looks_like_person
    n_to_indiv = _reclassify(
        df, to_individual, "committee_to_individual_last_first",
        individual=True,
    )
    to_committee = df["is_individual"] & has_org_keywords & ~looks_like_person
    n_to_comm = _reclassify(
        df, to_committee, "individual_to_committee_org_keywords",
        individual=False,
    )
    n_to_comm += _reclass_individual_nameless_org(df, initial_names)
    n_to_comm += _reclass_individual_ghost(df)
    n_to_comm += _reclass_definite_committees(df, initial_names)

    individuals = df["is_individual"]
    names = df["contributor_name"].fillna("").astype(str)
    # Existing committee labels need stronger business evidence to change.
    entered_as_committee = df["committee_type"].isin(_POLITICAL_COMMITTEE_TYPES)
    has_committee_signal = names.str.contains(_COMMITTEE_SIGNAL_RE, na=False)
    strong_business = (
        names.str.contains(_STRONG_BUSINESS_RE, na=False) & ~has_committee_signal
    )
    organizations = (
        ~individuals
        & ~has_committee_signal
        & names.str.contains(_ORG_BUSINESS_RE, na=False)
        & (~entered_as_committee | strong_business)
    )
    df["entity_type"] = np.select(
        [individuals, organizations], ["INDIVIDUAL", "ORGANIZATION"], default="COMMITTEE/PAC"
    )

    _apply_source_entity_type(df)
    _enforce_entity_name_consistency(df)

    df.loc[df["entity_type"] == "ORGANIZATION", "occupation_category"] = "ORGANIZATION"
    df.loc[df["entity_type"] == "COMMITTEE/PAC", "occupation_category"] = "POLITICAL COMMITTEE"
    return n_to_indiv, n_to_comm


def _apply_source_entity_type(df: pd.DataFrame) -> int:
    """Prefer FEC's type and mark newly identified people for work-field restoration."""
    source = deciding_source_type(df)
    differs = source.ne("") & source.ne(df["entity_type"])
    if not differs.any():
        return 0
    to_person = differs & source.eq("INDIVIDUAL")
    df.loc[to_person, "_reclass_reason"] = "committee_to_individual_fec_source"
    df.loc[differs & ~to_person, "_reclass_reason"] = "fec_source_entity_type"
    df.loc[differs, "entity_type"] = source[differs]
    df.loc[differs, "is_individual"] = to_person[differs]
    return int(differs.sum())


def _reclassify(df: pd.DataFrame, mask: pd.Series, reason: str, individual: bool) -> int:
    count = int(mask.sum())
    if count:
        df.loc[mask, "_reclass_reason"] = reason
        df.loc[mask, "is_individual"] = individual
    return count


def _reclass_individual_nameless_org(df: pd.DataFrame, name_str: pd.Series) -> int:
    """Individuals with no first/last name and an org-like name pattern."""
    no_names = _blank(df["contributor_first_name"]) & _blank(
        df["contributor_last_name"]
    )
    mask = (
        df["is_individual"]
        & no_names
        & name_str.str.contains(_NAMELESS_ORG_RE, na=False)
    )
    return _reclassify(df, mask, "individual_to_committee_no_name_org_pattern", individual=False)


def _reclass_individual_ghost(df: pd.DataFrame) -> int:
    """Individuals with no name, no employer and no occupation are committees."""
    mask = (
        df["is_individual"]
        & _blank(df["contributor_first_name"])
        & _blank(df["contributor_last_name"])
        & _blank(df["contributor_employer"])
        & _blank(df["contributor_occupation"])
    )
    return _reclassify(df, mask, "individual_to_committee_no_identity", individual=False)


def _reclass_definite_committees(df: pd.DataFrame, name_str: pd.Series) -> int:
    """Names that are always committees (FRIENDS OF, PEOPLE FOR, ...)."""
    mask = df["is_individual"] & name_str.str.contains(_DEFINITE_COMM_RE, na=False)
    n_changed = _reclassify(df, mask, "individual_to_committee_definite_prefix", individual=False)
    for idx in df.loc[mask].index:
        name = df.at[idx, "contributor_name"]
        if "," in name:
            df.at[idx, "contributor_name"] = name.split(",")[0].strip()
        df.at[idx, "contributor_first_name"] = np.nan
        df.at[idx, "contributor_last_name"] = np.nan
    return n_changed


def _enforce_entity_name_consistency(df: pd.DataFrame) -> int:
    """Use ORGANIZATION for matching names unless FEC supplied a type."""
    org_names = set(
        df.loc[df["entity_type"] == "ORGANIZATION", "contributor_name"].dropna()
    )
    if not org_names:
        return 0
    fix = (
        (df["entity_type"] == "COMMITTEE/PAC")
        & df["contributor_name"].isin(org_names)
        & source_entity_type(df).eq("")
    )
    n_changed = int(fix.sum())
    if n_changed:
        df.loc[fix, "entity_type"] = "ORGANIZATION"
    return n_changed


def _restore_individual_occupations(
    df: pd.DataFrame,
    raw_occupations: pd.Series,
    raw_employers: pd.Series,
    log,
) -> None:
    """Restore filed occupations after reclassification as people."""
    has_occupation = raw_occupations.notna() & raw_occupations.astype(str).str.strip().ne("")
    if not has_occupation.any():
        return

    restored, _ = _normalize_text(
        raw_occupations[has_occupation],
        OCCUPATION_NORMALIZE,
        collapse_retire=True,
    )
    raw_employer, _ = _normalize_text(
        raw_employers[has_occupation],
        EMPLOYER_NORMALIZE,
        digits_only_before=EMPLOYER_STATUS_TEXT,
    )
    # Keep a field swap already established before reclassification.
    kept_swap = (
        df.loc[restored.index, "contributor_employer"].map(employer_rule_key)
        .eq(restored.map(employer_rule_key))
        & df.loc[restored.index, "contributor_employer"].map(is_real_employer)
    )
    swapped = raw_employer.isin(SWAP_JOB_TITLES) & (
        restored.str.contains(_ORG_BUSINESS_RE, na=False) | restored.eq("SELF-EMPLOYED") | kept_swap
    )
    restored.loc[swapped] = raw_employer.loc[swapped]
    restored = restored[~restored.isin(MISSING_VALUES) & restored.notna()]
    if restored.empty:
        return

    df.loc[restored.index, "contributor_occupation"] = restored
    df.loc[restored.index, "occupation_category"] = _categorize(restored)
    df.loc[restored.index, "occupation_status"] = "DISCLOSED"
    map_occupation_fixes(df, restored.index)

    log(
        f"  -> restored {len(restored):,} occupations for reclassified "
        "committee->individual records"
    )


def _restore_individual_employers(
    df: pd.DataFrame,
    reclassified: pd.Series,
    raw_employers: pd.Series,
) -> None:
    """Restore missing employers after reclassification."""
    has_employer = raw_employers.notna() & raw_employers.astype(str).str.strip().ne("")
    missing_employer = reclassified & (
        df["contributor_employer"].isna()
        | df["contributor_employer"].eq("NOT DISCLOSED")
    )
    restore = has_employer & missing_employer.loc[has_employer.index]
    if not restore.any():
        return

    restored, _ = _normalize_text(
        raw_employers[restore],
        EMPLOYER_NORMALIZE,
        digits_only_before=EMPLOYER_STATUS_TEXT,
    )
    restored = restored[restored.notna() & ~restored.isin(MISSING_VALUES)]
    if not restored.empty:
        df.loc[restored.index, "contributor_employer"] = restored


def _clear_individual_residue(df: pd.DataFrame) -> None:
    """Fix missing-work status and clear short invalid values."""
    is_individual = df["is_individual"]

    missing_status = is_individual & df["occupation_status"].eq("NOT_APPLICABLE")
    if missing_status.any():
        df.loc[missing_status, "occupation_status"] = "MISSING"

    missing_occupation = is_individual & df["contributor_occupation"].isna()
    if missing_occupation.any():
        df.loc[missing_occupation, "occupation_category"] = pd.NA
        df.loc[missing_occupation, "occupation_status"] = "MISSING"

    short_fields = (
        ("contributor_employer", OK_SHORT_EMPLOYERS),
        ("contributor_occupation", OK_SHORT_OCCUPATIONS),
    )
    for column, whitelist in short_fields:
        values = df[column]
        junk = (
            is_individual
            & values.notna()
            & values.str.len().le(2)
            & ~values.isin(whitelist)
        )
        if junk.any():
            df.loc[junk, column] = np.nan


def _restore_reclassified_committees(
    df: pd.DataFrame,
    raw_occ_backup: pd.Series,
    raw_emp_backup: pd.Series,
    log,
) -> None:
    """Restore work fields for committee rows reclassified as individuals."""
    reclassified = df["is_individual"] & df["_reclass_reason"].str.startswith(
        "committee_to_individual", na=False
    )
    if reclassified.any():
        raw_occupations = raw_occ_backup.loc[reclassified]
        raw_employers = raw_emp_backup.loc[reclassified]
        _restore_individual_occupations(df, raw_occupations, raw_employers, log)
        _restore_individual_employers(df, reclassified, raw_employers)
