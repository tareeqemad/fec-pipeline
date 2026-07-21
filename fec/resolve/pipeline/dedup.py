"""
Post-resolve address-cache deduplication.

After `step_ai_lookup` finishes, this module collapses cache entries that
resolved to the same address AND share a strict prefix relationship in
their canonical key.

Why this isn't in cleaning: cleaning has no resolved addresses to compare,
so it can only catch name-level variants. Cases like

    MORGAN LEWIS                       1701 Market Street  [HIGH]
    MORGAN LEWIS AND BOCKIUS           1701 Market Street  [HIGH]
    MORGAN LEWIS BOCKIUS LLP           1701 Market Street  [HIGH]

are obvious duplicates only AFTER the AI has returned the same address
for all three, so they merge here.

Safety rule: the strict canonical keys of the two names must be in a
prefix relationship, AND the longer must be at most 2× the shorter. That
catches the Morgan-Lewis case but rejects e.g. "MCGUIREWOODS" vs
"BECKERS HEALTHCARE AND MCGUIREWOODS" (different prefixes).
"""
import re
from collections import defaultdict
from typing import Tuple

from fec.cleaning.employer_synonyms import canonical_key
from fec.log import get_logger

logger = get_logger(__name__)


def _same_entity(a: str, b: str) -> bool:
    """True if two employer names plausibly refer to the same company."""
    ka, kb = canonical_key(a), canonical_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = sorted([ka, kb], key=len)
    if not long_.startswith(short):
        return False
    return len(long_) <= 2 * len(short)


def _addr_norm(entry: dict) -> str:
    """Normalize a cached address for grouping."""
    addr = (entry.get('employer_address') or '').strip().upper()
    if not addr:
        return ''
    return re.sub(r'[^A-Z0-9]+', ' ', addr).strip()


def _score(entry: dict) -> tuple:
    """Higher = better. Prefer rows with addresses, then HIGH > MEDIUM > LOW.
    Web-search-grounded answers (ai_*_search, from the state-mismatch recheck)
    outrank closed-book recall — they were verified against live sources."""
    has_addr = 1 if entry.get('employer_address') else 0
    conf_rank = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}.get(entry.get('confidence', ''), 0)
    method = entry.get('method', '')
    if method in ('ai_openai_search', 'ai_xai_search'):
        method_pri = 3
    elif method in ('ai_openai', 'ai_openai_gpt5', 'ai_xai'):
        method_pri = 2
    elif method:
        method_pri = 1
    else:
        method_pri = 0
    return (has_addr, conf_rank, method_pri)


def dedup_by_resolved_address(addr_cache, freq: dict | None = None) -> Tuple[int, int, dict]:
    """
    Merge cache entries that have the same resolved address AND are
    plausibly the same entity.

    Args:
        addr_cache: the resolve address cache
        freq: optional {NAME_UPPER -> donor_row_count} from current CSV.
              When provided, the most-frequent variant wins as canonical
              (best rule — preserves the form most donors actually wrote).
              Falls back to longest-name + alphabetical tie-break.

    Returns: (merged_count, canonical_picks, mapping)
        merged_count    — entries removed from cache
        canonical_picks — number of merge groups processed
        mapping         — variant_name -> canonical_name (for caller to apply
                          to dataframe / CSV)
    """
    freq = freq or {}
    # Group entries by normalized address (only HIGH/MEDIUM confidence)
    by_addr: dict[str, list[str]] = defaultdict(list)
    for name, entry in addr_cache.data.items():
        if not isinstance(entry, dict):
            continue
        if entry.get('alias_of'):
            continue   # alias spellings mirror their canonical — already merged
        if entry.get('method') == 'manual_override':
            # A manual override is an authoritative human decision keyed to the
            # exact spelling in manual_employer_addresses.csv. Merging it away
            # made Step 0 re-create it on every run (7 updated -> 7 merged ->
            # repeat forever) and demoted human data to the AI canonical's.
            continue
        if entry.get('confidence') not in ('HIGH', 'MEDIUM'):
            continue
        addr = _addr_norm(entry)
        if not addr:
            continue
        by_addr[addr].append(name)

    mapping: dict[str, str] = {}  # variant_name -> canonical_name
    groups = 0
    for addr, names in by_addr.items():
        if len(names) < 2:
            continue
        # Within an address group, cluster names that pass _same_entity
        clusters: list[list[str]] = []
        for n in names:
            placed = False
            for cluster in clusters:
                if any(_same_entity(n, m) for m in cluster):
                    cluster.append(n)
                    placed = True
                    break
            if not placed:
                clusters.append([n])

        for cluster in clusters:
            if len(cluster) < 2:
                continue
            groups += 1
            # Canonical = most-frequently-written variant (what donors
            # actually filed). Falls back to longest, then alphabetical.
            # Without frequency data this would pick a short name like just
            # "GOLDMAN" — but with frequency, the canonical is whatever
            # 231 Goldman donors actually wrote ("GOLDMAN SACHS"), not a
            # rarer division name ("GOLDMAN SACHS NEW JERSEY") that
            # happened to also share the HQ address.
            canonical = sorted(
                cluster,
                key=lambda v: (-freq.get(v, 0), -len(v), v)
            )[0]
            for variant in cluster:
                if variant != canonical:
                    mapping[variant] = canonical

    if not mapping:
        return 0, 0, {}

    # Apply, two passes. First settle each canonical with the best-scored
    # data among its variants…
    removed = 0
    for variant, canonical in mapping.items():
        if variant not in addr_cache.data:
            continue
        v_entry = addr_cache.data[variant]
        if canonical in addr_cache.data:
            c_entry = addr_cache.data[canonical]
            if _score(v_entry) > _score(c_entry):
                addr_cache.data[canonical] = v_entry
        else:
            addr_cache.data[canonical] = v_entry
        removed += 1

    # …then keep each variant as an ALIAS carrying the canonical's address.
    # DELETING variants looked clean but created an infinite re-resolve loop:
    # previous_employer lookups still use the variant spelling (prev_cache
    # values are never remapped), so the deleted key made _needs_ai re-ask
    # the AI on every run — and dedup deleted the fresh answer again. The
    # same ~20 employers burned API tokens on every `resolve.py` run.
    # An alias keeps every spelling resolvable, and the grouping above skips
    # aliases, so the merge is idempotent (second run: 0 merged).
    for variant, canonical in mapping.items():
        c_entry = addr_cache.data.get(canonical)
        if not isinstance(c_entry, dict):
            continue
        alias = {k: v for k, v in c_entry.items() if k != 'alias_of'}
        alias['alias_of'] = canonical
        addr_cache.data[variant] = alias

    addr_cache.save()
    return removed, groups, mapping
