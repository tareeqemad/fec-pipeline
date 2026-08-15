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


def _address_key(entry: dict) -> str:
    """Normalize a complete cached address."""
    fields = (
        entry.get('employer_address'),
        entry.get('employer_city'),
        entry.get('employer_state'),
        entry.get('employer_zip'),
    )
    if not fields[0]:
        return ''
    return '|'.join(
        re.sub(r'[^A-Z0-9]+', ' ', str(value or '').upper()).strip()
        for value in fields
    )


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


def _entries_by_address(data: dict) -> dict[str, list[str]]:
    """Group eligible cache names by complete normalized address."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for name, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if entry.get('alias_of'):
            continue
        if entry.get('method') == 'manual_override':
            continue
        if entry.get('confidence') not in ('HIGH', 'MEDIUM'):
            continue
        address = _address_key(entry)
        if address:
            grouped[address].append(name)
    return grouped


def _cluster_company_names(names: list[str]) -> list[list[str]]:
    """Cluster same-company spellings while preserving cache order."""
    clusters: list[list[str]] = []
    for name in names:
        for cluster in clusters:
            if any(_same_entity(name, member) for member in cluster):
                cluster.append(name)
                break
        else:
            clusters.append([name])
    return clusters


def _canonical_name(cluster: list[str], frequency: dict) -> str:
    """Prefer the common filing spelling, then longest, then alphabetical."""
    return min(
        cluster,
        key=lambda name: (-frequency.get(name, 0), -len(name), name),
    )


def _alias_mapping(
    grouped: dict[str, list[str]], frequency: dict,
) -> tuple[dict[str, str], int]:
    """Return variant-to-canonical aliases and the number of merged groups."""
    mapping: dict[str, str] = {}
    groups = 0
    for names in grouped.values():
        if len(names) < 2:
            continue
        for cluster in _cluster_company_names(names):
            if len(cluster) < 2:
                continue
            groups += 1
            canonical = _canonical_name(cluster, frequency)
            mapping.update(
                {name: canonical for name in cluster if name != canonical}
            )
    return mapping, groups


def _settle_canonical_entries(data: dict, mapping: dict[str, str]) -> None:
    """Give each canonical name the strongest entry among its aliases."""
    for variant, canonical in mapping.items():
        if _score(data[variant]) > _score(data[canonical]):
            data[canonical] = data[variant]


def _write_aliases(data: dict, mapping: dict[str, str]) -> None:
    """Keep every spelling resolvable without triggering another lookup."""
    for variant, canonical in mapping.items():
        canonical_entry = data[canonical]
        alias = {
            key: value
            for key, value in canonical_entry.items()
            if key != 'alias_of'
        }
        alias['alias_of'] = canonical
        data[variant] = alias


def dedup_by_resolved_address(
    addr_cache, freq: dict | None = None,
) -> tuple[int, int]:
    """Alias same-company entries resolved to the same complete address."""
    mapping, groups = _alias_mapping(
        _entries_by_address(addr_cache.data),
        freq or {},
    )

    if not mapping:
        return 0, 0

    _settle_canonical_entries(addr_cache.data, mapping)
    _write_aliases(addr_cache.data, mapping)
    addr_cache.save()
    return len(mapping), groups
