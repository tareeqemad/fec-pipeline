"""Post-resolve cache dedup: entries that resolved to the same address and are plausibly the same company merge only after the AI returns matching addresses - cleaning can't see this."""
import re
from collections import defaultdict

from fec.cleaning.employer_synonyms import canonical_key
from fec.log import get_logger

logger = get_logger(__name__)


def _same_entity(a: str, b: str) -> bool:
    """Same company: canonical keys equal, or in a prefix relationship with the longer at most 2x the shorter (catches MORGAN LEWIS / MORGAN LEWIS BOCKIUS LLP, rejects unrelated prefixes)."""
    key_a, key_b = canonical_key(a), canonical_key(b)
    if not key_a or not key_b:
        return False
    if key_a == key_b:
        return True
    short, long_ = sorted([key_a, key_b], key=len)
    if not long_.startswith(short):
        return False
    return len(long_) <= 2 * len(short)


def _addr_norm(entry: dict) -> str:
    """Normalize a cached address for grouping."""
    address = (entry.get('employer_address') or '').strip().upper()
    if not address:
        return ''
    return re.sub(r'[^A-Z0-9]+', ' ', address).strip()


def _score(entry: dict) -> tuple:
    """Higher = better: has address, then confidence, then method - ai_*_search outranks closed-book (verified against live sources)."""
    has_address = 1 if entry.get('employer_address') else 0
    confidence_rank = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}.get(entry.get('confidence', ''), 0)
    method = entry.get('method', '')
    # by pattern, not by model name, so a new model's methods rank correctly;
    # ai_not_found is an AI MISS and must stay in the generic tier
    if method.startswith('ai_') and method.endswith('_search'):
        method_priority = 3
    elif method.startswith('ai_') and not method.endswith('_not_found'):
        method_priority = 2
    elif method:
        method_priority = 1
    else:
        method_priority = 0
    return (has_address, confidence_rank, method_priority)


def dedup_by_resolved_address(addr_cache, freq: dict | None = None) -> tuple[int, int, dict]:
    """Merge same-address same-entity cache entries; returns (removed, groups, variant->canonical mapping) with the most-frequent CSV variant winning as canonical."""
    freq = freq or {}
    by_address: dict[str, list[str]] = defaultdict(list)
    for name, entry in addr_cache.data.items():
        if not isinstance(entry, dict):
            continue
        if entry.get('alias_of'):
            continue   # alias spellings mirror their canonical - already merged
        if entry.get('method') == 'manual_override':
            # Never merge: keyed to the exact CSV spelling; merging made Step 0
            # re-create it every run and demoted human data to the AI canonical's.
            continue
        if entry.get('confidence') not in ('HIGH', 'MEDIUM'):
            continue
        address = _addr_norm(entry)
        if not address:
            continue
        by_address[address].append(name)

    mapping: dict[str, str] = {}  # variant_name -> canonical_name
    groups = 0
    for address, names in by_address.items():
        if len(names) < 2:
            continue
        clusters: list[list[str]] = []
        for name in names:
            placed = False
            for cluster in clusters:
                if any(_same_entity(name, member) for member in cluster):
                    cluster.append(name)
                    placed = True
                    break
            if not placed:
                clusters.append([name])

        for cluster in clusters:
            if len(cluster) < 2:
                continue
            groups += 1
            # Canonical = most-frequently-filed variant, then longest, then
            # alphabetical - frequency keeps "GOLDMAN SACHS" over a rare
            # division name that happens to share the HQ address.
            canonical = sorted(
                cluster,
                key=lambda variant: (-freq.get(variant, 0), -len(variant), variant)
            )[0]
            for variant in cluster:
                if variant != canonical:
                    mapping[variant] = canonical

    if not mapping:
        return 0, 0, {}

    # Pass 1: settle each canonical with the best-scored data among its variants.
    removed = 0
    for variant, canonical in mapping.items():
        variant_entry = addr_cache.data[variant]
        if _score(variant_entry) > _score(addr_cache.data[canonical]):
            addr_cache.data[canonical] = variant_entry
        removed += 1

    # Pass 2: keep each variant as an ALIAS of the canonical. Deleting variants
    # caused an infinite re-resolve loop (previous_employer lookups use the
    # variant spelling, so _needs_ai re-asked the AI every run); aliases keep
    # every spelling resolvable and the grouping above skips them (idempotent).
    for variant, canonical in mapping.items():
        canonical_entry = addr_cache.data[canonical]
        alias = {key: value for key, value in canonical_entry.items() if key != 'alias_of'}
        alias['alias_of'] = canonical
        addr_cache.data[variant] = alias

    addr_cache.save()
    return removed, groups, mapping
