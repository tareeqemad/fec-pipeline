"""Merge-chain checks and forced profile merges."""
from collections import defaultdict

from fec.donor_match.components import has_signal
from fec.donor_match.components import UnionFind
from fec.donor_match.rules import NAME_MERGES
from fec.donor_match.scoring import compute_score

# eject large-cluster members scoring below this
CHAIN_MIN = 30


CHAIN_CLUSTER_MIN = 4


# fail if a merge broke identity rules
def validate_merge_audit(rid_to_key: dict, audit_log: list) -> dict:
    final_merges = [
        row
        for row in audit_log
        if row["merged"]
        and rid_to_key.get(row["rid_a"]) == rid_to_key.get(row["rid_b"])
    ]
    violations = []

    for row in final_merges:
        street = has_signal(row, "street(")
        zip5 = has_signal(row, "zip5=")
        employer = has_signal(row, "employer=")
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
        elif has_signal(row, "cross_name(") and not (street or zip5 or employer):
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
            has_signal(row, "cross_name(")
            and has_signal(row, "zip5=")
            and not has_signal(row, "street(")
            and not has_signal(row, "employer=")
            for row in final_merges
        ),
    }


# build clusters, eject weak large-cluster members
def build_and_validate_chains(
    uf: UnionFind,
    profiles: dict,
    name_groups: dict,
) -> dict:
    components = defaultdict(set)
    for rid in uf.parent:
        root = uf.find(rid)
        components[root].add(rid)

    for root, members in list(components.items()):
        if len(members) < CHAIN_CLUSTER_MIN:
            continue

        # rid tie-break: set order is hash-randomized
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
            # ejected root clobbers survivors; existing keys depend
            for rid in ejected:
                components[rid] = {rid}

    return components


# fold components holding these rids into one
def _merge_roots_of(components: dict, rids: list) -> int:
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


# apply verified name merges
def apply_force_merges(components: dict, profiles: dict) -> None:
    for prefixes in NAME_MERGES.values():
        rids = [rid for rid, p in profiles.items() if p["name"].startswith(prefixes)]
        if len(rids) >= 2:
            _merge_roots_of(components, rids)
