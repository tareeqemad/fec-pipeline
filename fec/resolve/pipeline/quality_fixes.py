"""Post-apply quality fixes for resolved employer addresses."""

import re

import pandas as pd

from fec.cleaning.previous_employer import normalize_previous_employer_column

US_STATES = frozenset({
    'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA',
    'KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
    'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT',
    'VA','WA','WV','WI','WY','DC','PR','VI','GU','AS','MP',
})

_US_ZIP_RE = re.compile(r'^\d{5}(-\d{4})?$')

_PO_BOX_RE = re.compile(r'P\.?O\.?\s*BOX|POST\s*OFFICE\s*BOX', re.IGNORECASE)

# Closed-book AI resolve methods. The ai_*_search variants were verified
# against live sources, so every filter below exempts them.
_AI_METHOD_RE = re.compile(r'ai_(?:openai|xai)')

_AI_PLACEHOLDER_NUMBERS = frozenset({
    '100', '101', '123', '200', '234', '300', '345',
    '400', '456', '500', '567', '600', '678', '700',
    '789', '800', '890', '900', '999', '1000', '1234',
    '2345', '3456', '4567', '5678', '9999',
})

# Sequential / keyboard-walk / repeated-digit numbers are almost never real
# street numbers - diagnostic on their own, no token overlap needed.
_AI_UNAMBIGUOUS_FAKE_NUMBERS = frozenset({
    '1234', '2345', '3456', '4567', '5678', '6789', '7890',
    '4321', '8765', '9876', '5432', '6543', '7654',
    '12345', '23456', '34567', '45678', '56789',
    '54321', '65432', '76543', '87654', '98765',
    '1111', '2222', '3333', '4444', '5555', '6666',
    '7777', '8888', '9999',
    '9101',  # seen in samples: '9101 E 22nd St'
})

_AI_STATUS_WORD_EMPLOYERS = frozenset({
    'RETIRED', 'NOT EMPLOYED', 'UNEMPLOYED', 'SELF-EMPLOYED',
    'SELF EMPLOYED', 'HOMEMAKER', 'STUDENT', 'NOT DISCLOSED',
    'NONE', 'N/A', 'NA', 'NAN', '',
})

_AI_STREET_STOPWORDS = frozenset({
    'STREET', 'ST', 'AVENUE', 'AVE', 'ROAD', 'RD', 'DRIVE', 'DR',
    'LANE', 'LN', 'COURT', 'CT', 'PLACE', 'PL', 'BOULEVARD', 'BLVD',
    'PLAZA', 'CENTER', 'CENTRE', 'PARK', 'PARKWAY', 'PKWY',
    'NORTH', 'SOUTH', 'EAST', 'WEST', 'N', 'S', 'E', 'W',
    'SUITE', 'STE', 'FLOOR', 'FL', 'BUILDING', 'BLDG',
    'CORPORATION', 'CORP', 'COMPANY', 'CO', 'INC', 'LLC', 'LLP',
    'LTD', 'GROUP', 'ENTERPRISES', 'PARTNERS', 'THE', 'OF',
    'AND', 'OR', 'A', 'AN',
})

_AI_WORD_RE = re.compile(r'\b[A-Z]{2,}\b')
_AI_NUM_RE  = re.compile(r'^\s*(\d+)')


def _clear_nonindividual_employer(df: pd.DataFrame) -> None:
    """Null employer_* for non-individuals in-place - only individuals have an employer."""
    if 'entity_type' not in df.columns:
        return
    non_individual = df['entity_type'] != 'INDIVIDUAL'
    if not non_individual.any():
        return
    for col in ('employer_address', 'employer_city', 'employer_state',
                'employer_zip', 'employer_latitude', 'employer_longitude'):
        if col in df.columns:
            df.loc[non_individual, col] = pd.NA
    if 'employer_geocode_level' in df.columns:
        df.loc[non_individual, 'employer_geocode_level'] = 'no_address'
    df.loc[non_individual, 'resolve_method'] = 'skip'
    df.loc[non_individual, 'resolve_confidence'] = 'NONE'


def _fix_employer_address_quality(df: pd.DataFrame) -> None:
    """Fix known AI resolution quality issues in-place."""
    # 1. Foreign addresses cleared ENTIRELY - a kept foreign city/zip would
    # geocode to a US namesake (London->CT, Paris->KY). Foreign = non-US state
    # code, or non-US ZIP when the state isn't a confirmed US state; the US
    # territories in US_STATES count as US.
    emp_state = df['employer_state'].fillna('').astype(str).str.strip().str.upper()
    emp_zip = df['employer_zip'].fillna('').astype(str).str.strip()
    us_state_ok = emp_state.isin(US_STATES)
    non_us_state = (emp_state != '') & ~us_state_ok
    non_us_zip = (emp_zip != '') & ~emp_zip.str.match(_US_ZIP_RE, na=False)
    foreign = non_us_state | (non_us_zip & ~us_state_ok)
    if foreign.any():
        for col in ('employer_address', 'employer_city', 'employer_state', 'employer_zip'):
            df.loc[foreign, col] = ''
        for col in ('employer_latitude', 'employer_longitude', 'employer_geocode_level'):
            if col in df.columns:
                df.loc[foreign, col] = pd.NA
        df.loc[foreign, 'resolve_method'] = 'non_us_cleared'
        df.loc[foreign, 'resolve_confidence'] = 'NONE'

    # 2. PO Box is wrong for a corporate HQ but is the FEC-registered address
    # of most campaign committees - only clear non-committee rows.
    emp_addr = df['employer_address'].fillna('')
    is_po_box = emp_addr.str.contains(_PO_BOX_RE, na=False)
    # Closed-book AI only - a web-search-grounded (_search) PO box is the
    # firm's verified public address; keep it (still geocodes at ZIP level).
    method_col = df['resolve_method'].fillna('').astype(str)
    is_ai = (method_col.str.contains(_AI_METHOD_RE, na=False)
             & ~method_col.str.contains('_search', na=False))
    not_committee = df['entity_type'].fillna('') != 'COMMITTEE/PAC'
    ai_po_box = is_po_box & is_ai & not_committee
    if ai_po_box.any():
        df.loc[ai_po_box, 'employer_address'] = ''
        df.loc[ai_po_box, 'employer_city'] = ''
        df.loc[ai_po_box, 'employer_state'] = ''
        df.loc[ai_po_box, 'employer_zip'] = ''
        df.loc[ai_po_box, 'resolve_method'] = 'ai_po_box_cleared'
        df.loc[ai_po_box, 'resolve_confidence'] = 'NONE'

    # 3. The resolve cache's previous_employer values are uncleaned, and older
    # CSVs may carry stale un-normalized entries.
    _normalize_previous_employer_column(df)

    # 4. AI-hallucinated addresses - patterns documented in the function.
    _clear_ai_hallucinated_addresses(df)

    # 5. employer_status is populated only by apply_results, so clean.py's
    # retired+active sweep runs too early to see these rows - re-sync here
    # in the same pass that creates the contradiction (idempotent).
    from fec.database.post_merge_fixes import _retired_active_sync
    _retired_active_sync(df)


def _normalize_previous_employer_column(df: pd.DataFrame) -> None:
    """Apply the shared previous_employer contract (fec/cleaning/previous_employer.py) so every writer uses the same rules."""
    normalize_previous_employer_column(df)


def _clear_ai_hallucinated_addresses(df: pd.DataFrame) -> None:
    """Clear AI-fabricated employer_address rows in-place (method 'ai_hallucination_cleared')."""
    if 'resolve_method' not in df.columns or 'employer_address' not in df.columns:
        return

    # Closed-book recall only. ai_*_search answers were verified against live
    # sources - second-guessing them makes false positives: real firms sit on
    # eponymous streets and real buildings have fake-looking numbers.
    method = df['resolve_method'].fillna('').astype(str)
    ai = (method.str.contains(_AI_METHOD_RE, na=False)
          & ~method.str.contains('_search', na=False))
    if not ai.any():
        return

    def _words(value):
        if pd.isna(value):
            return set()
        return {token for token in _AI_WORD_RE.findall(str(value).upper())
                if len(token) >= 4 and token not in _AI_STREET_STOPWORDS}

    def _num(value):
        if pd.isna(value):
            return ''
        match = _AI_NUM_RE.match(str(value))
        return match.group(1) if match else ''

    # Pattern A: placeholder street number + street name echoing the employer.
    street_number = df['employer_address'].map(_num)
    address_words = df['employer_address'].map(_words)
    employer_words = df.apply(
        lambda row: _words(row.get('contributor_employer')) | _words(row.get('previous_employer')),
        axis=1,
    )
    placeholder_num = street_number.isin(_AI_PLACEHOLDER_NUMBERS)
    word_overlap = pd.Series(
        [bool(employer_set & address_set)
         for employer_set, address_set in zip(employer_words, address_words)],
        index=df.index,
    )

    # Pattern B: status-word employer (RETIRED etc.) with no previous_employer.
    employer_col = df['contributor_employer'].fillna('')
    prev_empty = df.get('previous_employer', pd.Series('', index=df.index)).fillna('').eq('')
    status_no_prev = employer_col.isin(_AI_STATUS_WORD_EMPLOYERS) & prev_empty

    # Pattern C: committee address shared by 2+ committees. Real committee HQs
    # are unique; a shared address is the AI's default placeholder.
    is_committee = df['entity_type'].eq('COMMITTEE/PAC')
    ai_committee = ai & is_committee
    if ai_committee.any():
        shared = df.loc[ai_committee].groupby('employer_address')['contributor_name'].nunique()
        shared_addresses = set(shared[shared >= 2].index)
        shared_addresses.discard('')
        shared_addresses.discard(None)
        committee_placeholder = ai_committee & df['employer_address'].isin(shared_addresses)
    else:
        committee_placeholder = pd.Series(False, index=df.index)

    # Pattern D (>=100-repeats = placeholder) disabled: it wiped ~25 real HQs
    # (Kirkland, Goldman, Morgan Stanley) and caught zero true hallucinations.

    # Pattern E: unambiguously-fake leading number, cleared without overlap or
    # volume checks - catches fakes whose street shares no tokens with the employer.
    unambiguous_fake_num = street_number.isin(_AI_UNAMBIGUOUS_FAKE_NUMBERS)

    hallucinated = ai & (
        (placeholder_num & word_overlap)
        | status_no_prev
        | committee_placeholder
        | unambiguous_fake_num
    )
    n_cleared = int(hallucinated.sum())
    if not n_cleared:
        return

    for col in ('employer_address', 'employer_city', 'employer_state', 'employer_zip',
                'employer_latitude', 'employer_longitude', 'employer_geocode_level'):
        if col in df.columns:
            df.loc[hallucinated, col] = pd.NA
    df.loc[hallucinated, 'resolve_method'] = 'ai_hallucination_cleared'
    df.loc[hallucinated, 'resolve_confidence'] = 'NONE'
