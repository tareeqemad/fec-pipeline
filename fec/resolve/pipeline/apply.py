"""Apply resolved addresses to DataFrame columns."""

import re

import pandas as pd

from fec.cleaning.employer_synonyms import (
    normalize_employer_display_name,
    EMPLOYER_SYNONYMS,
)
from fec.cleaning.previous_employer import normalize_previous_employer_column
from .constants import RETIRED_VALUES, SELF_EMPLOYED_VALUES
from .helpers import _s, _prev_key, _is_real_employer

US_STATES = frozenset({
    'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA',
    'KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
    'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT',
    'VA','WA','WV','WI','WY','DC','PR','VI','GU','AS','MP',
})


def apply_results(df: pd.DataFrame, prev_cache, addr_cache, comm_cache) -> pd.DataFrame:
    """Write resolved addresses to DataFrame columns."""

    cols = {
        "employer_address": [], "employer_city": [],
        "employer_state": [], "employer_zip": [],
        "resolve_method": [], "resolve_confidence": [],
        "employer_status": [], "previous_employer": [],
    }

    for _, row in df.iterrows():
        result = _resolve_row(row, prev_cache, addr_cache, comm_cache)
        for k in cols:
            cols[k].append(result.get(k, ""))

    for k, vals in cols.items():
        df[k] = vals

    # Post-processing: fix AI data quality issues
    _fix_employer_address_quality(df)

    # Only individuals have an "employer". A committee or organization IS the
    # entity itself — its own address is already stored once as the
    # contributor/donor address. Don't duplicate it into the employer_* columns
    # (non-individuals get no employer record in the DB; the map reads donor
    # coords).
    _clear_nonindividual_employer(df)

    return df


def _clear_nonindividual_employer(df: pd.DataFrame) -> None:
    """Null employer_* for non-individuals — they have no employer (in-place)."""
    if 'entity_type' not in df.columns:
        return
    comm = df['entity_type'] != 'INDIVIDUAL'
    if not comm.any():
        return
    for col in ('employer_address', 'employer_city', 'employer_state',
                'employer_zip', 'employer_latitude', 'employer_longitude'):
        if col in df.columns:
            df.loc[comm, col] = pd.NA
    if 'employer_geocode_level' in df.columns:
        df.loc[comm, 'employer_geocode_level'] = 'no_address'
    df.loc[comm, 'resolve_method'] = 'skip'
    df.loc[comm, 'resolve_confidence'] = 'NONE'


def _fix_employer_address_quality(df: pd.DataFrame) -> None:
    """Fix known AI resolution quality issues in-place."""

    # 1. Non-US employer addresses. Donors are US-based, so we want each
    # company's US office — never a foreign HQ (RBC->Toronto, Burberry->London,
    # Wipro->Bengaluru). A foreign address must be cleared ENTIRELY: keeping the
    # foreign city/zip makes the geocoder match the city to a US namesake
    # (London->CT, Dublin->OH, Paris->KY, Vancouver->WA), planting the firm at a
    # wrong US point. Detect foreign by a non-US state code OR a non-US ZIP
    # (letters like "EC4Y 0JP" / "M5X 1A1", or non-5-digit like "560035") when
    # the state isn't a confirmed US state. A valid US state with an odd ZIP is
    # left alone. US territories (PR/VI/GU/AS/MP) count as US (in US_STATES).
    emp_state = df['employer_state'].fillna('').astype(str).str.strip().str.upper()
    emp_zip = df['employer_zip'].fillna('').astype(str).str.strip()
    us_state_ok = emp_state.isin(US_STATES)
    non_us_state = (emp_state != '') & ~us_state_ok
    non_us_zip = (emp_zip != '') & ~emp_zip.str.match(r'^\d{5}(-\d{4})?$', na=False)
    foreign = non_us_state | (non_us_zip & ~us_state_ok)
    if foreign.any():
        for col in ('employer_address', 'employer_city', 'employer_state', 'employer_zip'):
            df.loc[foreign, col] = ''
        for col in ('employer_latitude', 'employer_longitude', 'employer_geocode_level'):
            if col in df.columns:
                df.loc[foreign, col] = pd.NA
        df.loc[foreign, 'resolve_method'] = 'non_us_cleared'
        df.loc[foreign, 'resolve_confidence'] = 'NONE'

    # 2. PO Box in AI-resolved EMPLOYER addresses. A PO Box is wrong for a
    # corporate HQ — but it is the correct, FEC-registered address for most
    # candidate campaign committees, so only clear it for non-committee rows.
    po_pattern = re.compile(r'P\.?O\.?\s*BOX|POST\s*OFFICE\s*BOX', re.IGNORECASE)
    emp_addr = df['employer_address'].fillna('')
    is_po = emp_addr.str.contains(po_pattern, na=False)
    # Closed-book AI only — a web-search-grounded (_search) PO box is the
    # firm's VERIFIED public address (small companies often have no street
    # listing); keep it, it still geocodes at ZIP level.
    method_s = df['resolve_method'].fillna('').astype(str)
    is_ai = (method_s.str.contains(r'ai_(?:openai|xai)', na=False, regex=True)
             & ~method_s.str.contains('_search', na=False))
    not_committee = df['entity_type'].fillna('') != 'COMMITTEE/PAC'
    ai_po = is_po & is_ai & not_committee
    if ai_po.any():
        # Clear PO Box from AI results (AI should return physical addresses)
        df.loc[ai_po, 'employer_address'] = ''
        df.loc[ai_po, 'employer_city'] = ''
        df.loc[ai_po, 'employer_state'] = ''
        df.loc[ai_po, 'employer_zip'] = ''
        df.loc[ai_po, 'resolve_method'] = 'ai_po_box_cleared'
        df.loc[ai_po, 'resolve_confidence'] = 'NONE'

    # 3. previous_employer normalization — same rules contributor_employer
    # follows. Needed because the resolve cache's values aren't cleaned,
    # and existing CSVs may contain stale un-normalized entries from
    # before the normalization was wired in at write time.
    _normalize_previous_employer_column(df)

    # 4. AI-hallucinated employer addresses. OpenAI invents plausible
    # but fake addresses when handed ambiguous / generic employer
    # names ("HOUSING", "CAMPAIGN/COMMITTEE", "RETIRED"). Three
    # patterns are fabricated reliably enough to auto-clear:
    #   a) placeholder street number (100/123/456/1234/9999/...)
    #      combined with a street name that echoes the employer or
    #      previous_employer (e.g. GELLER AND COMPANY -> 567 Geller St)
    #   b) AI-resolved address on a status-word employer (RETIRED /
    #      NOT EMPLOYED / ...) that has no previous_employer set
    #   c) committee address reused across 2+ different committees
    #      (real committee HQs are unique; a shared address is the
    #      AI's default placeholder)
    #   d) the same employer_address appears >= 100 times under pure
    #      'ai_openai' (no FEC / manual / cross-record corroboration).
    #      Real HQs at that volume get verified and thus carry a
    #      composite resolve_method; a pure-AI address this repetitive
    #      is a cached hallucination reused across many donors.
    #   e) address begins with an unambiguously-fake number (sequential
    #      1234/5678/12345, keyboard-walk 4321, repeated 1111/9999,
    #      etc.). These prefixes are diagnostic on their own; cleared
    #      regardless of employer token overlap or volume.
    _clear_ai_hallucinated_addresses(df)

    # 5. retired + active contradiction. employer_status is populated
    #    only by apply_results (above) — so the post_merge_fixes AQ
    #    sweep in clean.py runs too early to see these rows. Re-invoke
    #    the sync here so the contradiction is resolved in the same
    #    pass that creates it. Idempotent either way.
    try:
        from fec.database.post_merge_fixes import _retired_active_sync
        _retired_active_sync(df)
    except Exception:  # pragma: no cover
        # Don't break resolve pipeline if post_merge_fixes import fails
        pass


_AI_PLACEHOLDER_NUMBERS = frozenset({
    '100', '101', '123', '200', '234', '300', '345',
    '400', '456', '500', '567', '600', '678', '700',
    '789', '800', '890', '900', '999', '1000', '1234',
    '2345', '3456', '4567', '5678', '9999',
})

# Numbers that are almost never real street numbers — sequential /
# keyboard-walk / repeated-digit patterns. Any address starting with
# one of these under pure 'ai_openai' can be cleared without requiring
# token overlap with the employer name; the pattern alone is diagnostic.
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


def _clear_ai_hallucinated_addresses(df: pd.DataFrame) -> None:
    """Clear AI-fabricated employer_address values in-place.

    See the three patterns documented in _fix_employer_address_quality.
    Values cleared: employer_address/city/state/zip + employer_lat/lng
    + employer_geocode_level. resolve_method set to
    'ai_hallucination_cleared'; resolve_confidence set to 'NONE'.
    """
    if 'resolve_method' not in df.columns or 'employer_address' not in df.columns:
        return

    # Heuristics below are for CLOSED-BOOK recall only. Web-search-grounded
    # answers (ai_*_search, from the state-mismatch recheck) were verified
    # against live sources — second-guessing them produces false positives:
    # real firms sit on eponymous streets ("400 Victory Drive" IS Victory
    # Wholesale's campus; "101 Army Pentagon" IS the Pentagon) and real
    # buildings have "fake-looking" numbers (2345 Grand Blvd, 1111 Douglas).
    method = df['resolve_method'].fillna('').astype(str)
    ai = (method.str.contains(r'ai_(?:openai|xai)', na=False, regex=True)
          & ~method.str.contains('_search', na=False))
    if not ai.any():
        return

    def _words(s):
        if pd.isna(s):
            return set()
        return {t for t in _AI_WORD_RE.findall(str(s).upper())
                if len(t) >= 4 and t not in _AI_STREET_STOPWORDS}

    def _num(s):
        if pd.isna(s):
            return ''
        m = _AI_NUM_RE.match(str(s))
        return m.group(1) if m else ''

    # Pattern A: placeholder number + word overlap
    num = df['employer_address'].map(_num)
    addr_w = df['employer_address'].map(_words)
    emp_w = df.apply(
        lambda r: _words(r.get('contributor_employer')) | _words(r.get('previous_employer')),
        axis=1,
    )
    placeholder_num = num.isin(_AI_PLACEHOLDER_NUMBERS)
    word_overlap = pd.Series(
        [bool(a & b) for a, b in zip(emp_w, addr_w)], index=df.index,
    )

    # Pattern B: status-word employer with no previous_employer
    emp_upper = df['contributor_employer'].fillna('').astype(str).str.strip().str.upper()
    prev_empty = df.get('previous_employer', pd.Series('', index=df.index)).fillna('').astype(str).str.strip().eq('')
    status_no_prev = emp_upper.isin(_AI_STATUS_WORD_EMPLOYERS) & prev_empty

    # Pattern C: committee address reused across multiple committees
    is_committee = df.get('entity_type', pd.Series('', index=df.index)).eq('COMMITTEE/PAC')
    ai_committee = ai & is_committee
    if ai_committee.any():
        shared = df.loc[ai_committee].groupby('employer_address')['contributor_name'].nunique()
        shared_addrs = set(shared[shared >= 2].index)
        shared_addrs.discard('')
        shared_addrs.discard(None)
        committee_placeholder = ai_committee & df['employer_address'].isin(shared_addrs)
    else:
        committee_placeholder = pd.Series(False, index=df.index)

    # Pattern D (removed): the original rule cleared any
    # address that appeared >= 100 times under pure 'ai_openai'. It was
    # designed for an earlier pipeline where real HQs got verified via
    # fec_api or manual_override and thus carried a composite
    # resolve_method — so "pure AI + high volume" was diagnostic.
    #
    # In the current pipeline, every employer HQ lookup is pure AI
    # (gpt-5-mini), so high volume is just a signal that the firm has
    # many donors — Kirkland & Ellis (575 donors at 300 N LaSalle),
    # Goldman Sachs (231 at 200 West St), Morgan Stanley (231 at 1585
    # Broadway), etc. all hit the threshold and were being wiped.
    # Empirically the rule caught zero true hallucinations and ~25 real
    # HQs in our data, so it's disabled. Patterns A, B, C, E still
    # catch the obvious fakes (placeholder numbers, status-word AI
    # results, fake-prefix street numbers).
    high_volume_placeholder = pd.Series(False, index=df.index)

    # Pattern E: unambiguously-fake leading number. Addresses starting
    # with sequential / keyboard / repeated-digit numbers (1234, 5678,
    # 12345, 1111, 9999, …) are practically never real street numbers.
    # Any AI-touched row with such a prefix is cleared — no word-overlap
    # or volume threshold needed. Covers the long tail of fake addresses
    # that slip past Pattern A when the street name shares no tokens
    # with the employer (AI writes "1234 W 25th St" for any employer).
    unambiguous_fake_num = num.isin(_AI_UNAMBIGUOUS_FAKE_NUMBERS)

    halluc = ai & (
        (placeholder_num & word_overlap)
        | status_no_prev
        | committee_placeholder
        | high_volume_placeholder
        | unambiguous_fake_num
    )
    n = int(halluc.sum())
    if not n:
        return

    for col in ('employer_address', 'employer_city', 'employer_state', 'employer_zip',
                'employer_latitude', 'employer_longitude', 'employer_geocode_level'):
        if col in df.columns:
            df.loc[halluc, col] = pd.NA
    df.loc[halluc, 'resolve_method'] = 'ai_hallucination_cleared'
    df.loc[halluc, 'resolve_confidence'] = 'NONE'


def _normalize_previous_employer_column(df: pd.DataFrame) -> None:
    """Apply the previous_employer contract to the column.

    The contract itself lives in fec/cleaning/previous_employer.py so the
    post-merge cleaning steps that also write this column use the SAME rules —
    otherwise the column's contents depend on which stage ran last.
    """
    normalize_previous_employer_column(df)


def _resolve_row(row: pd.Series, prev_cache, addr_cache, comm_cache) -> dict:
    """Resolve one row."""
    EMPTY = {
        "employer_address": "", "employer_city": "",
        "employer_state": "", "employer_zip": "",
        "resolve_method": "skip", "resolve_confidence": "NONE",
        "employer_status": "", "previous_employer": "",
    }

    entity = row.get("entity_type", "")
    emp = _s(row.get("contributor_employer")).strip()
    emp_upper = emp.upper()
    emp_norm = emp
    state = _s(row.get("contributor_state")).strip()

    # Committee
    if entity == "COMMITTEE/PAC":
        name = str(row.get("contributor_name", ""))
        key = f"{name}|{state}"
        cached = comm_cache.get(key)
        if cached and cached.get("employer_address"):
            return {
                "employer_address": cached["employer_address"],
                "employer_city": cached.get("employer_city", ""),
                "employer_state": cached.get("employer_state", state),
                "employer_zip": cached.get("employer_zip", ""),
                "resolve_method": cached.get("method", "fec_api"),
                "resolve_confidence": cached.get("confidence", "HIGH"),
                "employer_status": "committee",
                "previous_employer": "",
            }
        street = _s(row.get("contributor_street_1"))
        if street:
            return {
                "employer_address": street,
                "employer_city": _s(row.get("contributor_city")),
                "employer_state": state,
                "employer_zip": _s(row.get("contributor_zip")),
                "resolve_method": "committee_own_address",
                "resolve_confidence": "LOW",
                "employer_status": "committee",
                "previous_employer": "",
            }
        EMPTY["employer_status"] = "committee"
        return EMPTY

    # Organization — mirrors the committee rule: a non-individual's status names
    # its entity class. Orgs used to fall through to the individual logic and
    # come out 'missing', which reads as "a person whose job we don't know".
    # The org IS the entity; its own address is already the donor address, so
    # no employer_* lookup applies.
    if entity == "ORGANIZATION":
        EMPTY["employer_status"] = "organization"
        return EMPTY

    # Real employer — cache is keyed by EMPLOYER (corporate HQ, no per-state).
    if _is_real_employer(emp):
        key = emp_norm.upper()
        cached = addr_cache.get(key)
        # A manual_override is a deliberate human decision — honor it even
        # when only city/state is known (no verified street). AI / other
        # cache entries still require a street to count as resolved.
        if cached and (cached.get("employer_address") or (
            cached.get("method") == "manual_override" and cached.get("employer_city")
        )):
            return {
                "employer_address": cached.get("employer_address", ""),
                "employer_city": cached.get("employer_city", ""),
                "employer_state": cached.get("employer_state", ""),
                "employer_zip": cached.get("employer_zip", ""),
                "resolve_method": cached.get("method", "ai_openai"),
                "resolve_confidence": cached.get("confidence", "HIGH"),
                "employer_status": "active",
                "previous_employer": "",
            }
        EMPTY["employer_status"] = "active"
        EMPTY["resolve_method"] = "pending"
        return EMPTY

    # RETIRED
    if emp_upper in RETIRED_VALUES:
        pk = _prev_key(row.get("contributor_name", ""), state)
        prev = prev_cache.get(pk)
        if prev and prev.get("employer"):
            prev_name_raw = _s(prev["employer"]).strip()
            # Run previous_employer through the same normalization rules
            # that contributor_employer uses — the cache was never cleaned,
            # so "DuPont", "CHAPEL HAVEN, INC", "LATHAM & WATKINS LLP" etc.
            # would otherwise leak unnormalized into the output.
            prev_name = normalize_employer_display_name(prev_name_raw) or ""
            prev_norm = _s(prev.get("employer_normalized", prev_name_raw)).strip().upper()
            # Apply EMPLOYER_SYNONYMS dict so previous_employer lookups hit the
            # same cache keys as contributor_employer. Without this, retirees
            # of brands the dict promotes (e.g. WHATSAPP → "WHATSAPP LLC")
            # miss the address lookup even though it's in the cache.
            prev_norm = EMPLOYER_SYNONYMS.get(prev_norm, prev_norm)
            prev_name = EMPLOYER_SYNONYMS.get(prev_name.upper(), prev_name) or prev_name
            key = prev_norm
            cached = addr_cache.get(key)
            if cached and cached.get("employer_address"):
                return {
                    "employer_address": cached["employer_address"],
                    "employer_city": cached.get("employer_city", ""),
                    "employer_state": cached.get("employer_state", ""),
                    "employer_zip": cached.get("employer_zip", ""),
                    "resolve_method": f"{prev.get('method','cross_record')}+{cached.get('method','ai_openai')}",
                    "resolve_confidence": cached.get("confidence", "HIGH"),
                    "employer_status": "retired",
                    "previous_employer": prev_name,
                }
            return {
                "employer_address": "", "employer_city": "",
                "employer_state": "", "employer_zip": "",
                "resolve_method": "pending_address",
                "resolve_confidence": "NONE",
                "employer_status": "retired",
                "previous_employer": prev_name,
            }
        EMPTY["employer_status"] = "retired"
        return EMPTY

    # SELF-EMPLOYED -> use own address
    if emp_upper in SELF_EMPLOYED_VALUES:
        street = _s(row.get("contributor_street_1"))
        if street:
            return {
                "employer_address": street,
                "employer_city": _s(row.get("contributor_city")),
                "employer_state": state,
                "employer_zip": _s(row.get("contributor_zip")),
                "resolve_method": "self_employed_own_address",
                "resolve_confidence": "HIGH",
                "employer_status": "self_employed",
                "previous_employer": "",
            }
        EMPTY["employer_status"] = "self_employed"
        return EMPTY

    # NOT EMPLOYED / STUDENT / HOMEMAKER / empty -> SKIP
    EMPTY["employer_status"] = "not_employed" if emp_upper in (
        "NOT EMPLOYED", "STUDENT", "HOMEMAKER", "UNEMPLOYED"
    ) else "missing"
    return EMPTY


# Cross-state lookup removed — cache is now keyed by EMPLOYER
# (one corporate HQ per company), so there's no "other state" to fall back to.
