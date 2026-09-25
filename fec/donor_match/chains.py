"""Check merge chains and apply forced merges between profiles."""
from collections import defaultdict

from fec.donor_match.components import _has_signal
from fec.donor_match.components import UnionFind
from fec.donor_match.rules import NAME_MERGES
from fec.donor_match.scoring import compute_score

# chain validation: in clusters of CHAIN_CLUSTER_MIN+ members, anyone scoring
# below CHAIN_MIN against the canonical record is ejected
CHAIN_MIN = 30


CHAIN_CLUSTER_MIN = 4


def _validate_merge_audit(rid_to_key: dict, audit_log: list) -> dict:
    """Fail if a final automatic merge bypassed an identity safety rule."""
    final_merges = [
        row
        for row in audit_log
        if row["merged"]
        and rid_to_key.get(row["rid_a"]) == rid_to_key.get(row["rid_b"])
    ]
    violations = []

    for row in final_merges:
        street = _has_signal(row, "street(")
        zip5 = _has_signal(row, "zip5=")
        employer = _has_signal(row, "employer=")
        signals = row["signals"]

        if any(
            block in signals
            for block in (
                "HARD_BLOCK",
                "BLOCKED(",
                "CROSS_NAME_NO_ANCHOR",
            )
        ):
            violations.append(row)
        elif _has_signal(row, "cross_name(") and not (street or zip5 or employer):
            violations.append(row)
        elif "surname_variant" in signals and not (street or (zip5 and employer)):
            violations.append(row)
        elif "name_format_variant" in signals and not (street or zip5):
            violations.append(row)
        elif "surname_superset" in signals and not street:
            violations.append(row)

    if violations:
        examples = ", ".join(
            f"{row['rid_a']} <-> {row['rid_b']}" for row in violations[:3]
        )
        raise ValueError(
            f"Donor identity quality gate rejected {len(violations)} unsafe "
            f"merge(s): {examples}"
        )

    return {
        "checked": len(final_merges),
        "cross_name_zip_only": sum(
            _has_signal(row, "cross_name(")
            and _has_signal(row, "zip5=")
            and not _has_signal(row, "street(")
            and not _has_signal(row, "employer=")
            for row in final_merges
        ),
    }


def _build_and_validate_chains(
    uf: UnionFind,
    profiles: dict,
    name_groups: dict,
) -> dict:
    """Build components from union-find, then eject weakly-chained members of large clusters."""
    components = defaultdict(set)
    for rid in uf.parent:
        root = uf.find(rid)
        components[root].add(rid)

    for root, members in list(components.items()):
        if len(members) < CHAIN_CLUSTER_MIN:
            continue

        # The rid tie-break makes chain ejections and donor keys repeatable
        # across processes; set iteration order is hash-randomized.
        canonical_rid = max(
            members,
            key=lambda rid: (profiles[rid]["record_count"], rid),
        )
        p_canon = profiles[canonical_rid]
        canon_freq = len(name_groups[p_canon["norm_name"]])

        ejected = set()
        for rid in members:
            if rid == canonical_rid:
                continue
            p = profiles[rid]
            score, _ = compute_score(p_canon, p, canon_freq)
            if score < CHAIN_MIN:
                ejected.add(rid)

        if ejected:
            components[root] -= ejected
            # if the union-find root itself is ejected (root != canonical_rid),
            # components[rid] = {rid} below clobbers the survivors' set and they
            # fall back to per-rid keys in apply_donor_key. Reordering this would
            # change existing donor_key values, so it is documented, not changed.
            for rid in ejected:
                components[rid] = {rid}

    return components


def _merge_roots_of(components: dict, rids: list) -> int:
    """Fold every component containing one of rids into a single one; returns extra roots folded (0 when fewer than 2 distinct roots)."""
    roots = set()
    for rid in rids:
        for root, members in components.items():
            if rid in members:
                roots.add(root)
                break

    if len(roots) < 2:
        return 0

    roots_list = sorted(roots)
    target = roots_list[0]
    for other_root in roots_list[1:]:
        components[target] |= components[other_root]
        del components[other_root]
    return len(roots_list) - 1


def _apply_force_merges(components: dict, profiles: dict) -> None:
    """Apply verified name merges."""
    for prefixes in NAME_MERGES.values():
        rids = [rid for rid, p in profiles.items() if p["name"].startswith(prefixes)]
        if len(rids) >= 2:
            _merge_roots_of(components, rids)
