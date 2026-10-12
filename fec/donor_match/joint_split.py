"""Split joint filings off the filer's donor."""
from collections import defaultdict

from fec.donor_match.joint import first_of_name, given_tokens, joint_partners
from fec.donor_match.components import donor_keys
from fec.donor_match.rules import joint_name_exempt, resolve_donor_key


# get a profile's given-name token spelling
def _profile_spelling(profile: dict) -> tuple:
    return given_tokens(first_of_name(profile["name"]))


# get a profile's normalized surname
def _profile_last(profile: dict) -> str:
    return profile["norm_name"].partition("|")[0] if "|" in profile["norm_name"] else ""


# profiles whose first name names a co-filer
def find_joint_filings(profiles: dict, components: dict) -> dict:
    rid_to_key = donor_keys(components)
    person_of = {rid: resolve_donor_key(key) for rid, key in rid_to_key.items()}
    members = defaultdict(list)
    for rid, person in person_of.items():
        members[person].append(rid)

    spellings = {rid: _profile_spelling(p) for rid, p in profiles.items()}
    by_place = defaultdict(set)  # (surname, spot) to persons
    places = defaultdict(set)    # person to streets and zip5s
    for rid, person in person_of.items():
        profile = profiles[rid]
        last = _profile_last(profile)
        spots = {("S", street) for street in profile["streets"]}
        if len(profile["zip5"]) == 5:
            spots.add(("Z", profile["zip5"]))
        places[person] |= spots
        if last:
            for spot in spots:
                by_place[(last, spot)].add(person)

    signatures = {}
    for rid, person in person_of.items():
        spelling = spellings[rid]
        # one word: glued pair needs another spelling
        if not spelling or (len(spelling) < 2 and len(members[person]) < 2):
            continue
        profile = profiles[rid]
        if joint_name_exempt(profile["name"]):
            continue
        last = _profile_last(profile)
        others = set()
        for spot in places[person]:
            others |= by_place.get((last, spot), set())
        others.discard(person)
        if not others:
            continue
        household = {
            spellings[other_rid]
            for other in others
            for other_rid in members[other]
            if _profile_last(profiles[other_rid]) == last
        }
        own = {spellings[member] for member in members[person]}
        partners = joint_partners(spelling, own, household)
        if partners:
            signatures[rid] = partners
    return signatures


# give each joint filing its own donor
def split_joint_filings(components: dict, signatures: dict, profiles: dict) -> int:
    moved = 0
    for root, members in list(components.items()):
        parts = defaultdict(set)
        for rid in members:
            parts[signatures.get(rid, frozenset())].add(rid)
        if len(parts) < 2:
            continue
        del components[root]
        for signature, part in sorted(parts.items(), key=lambda item: sorted(item[0])):
            if root in part:
                new_root = root
            else:
                ranked = sorted(
                    part,
                    key=lambda rid: (profiles[rid]["record_count"], rid),
                    reverse=True,
                )
                new_root = next(rid for rid in ranked if rid not in components)
            components[new_root] = part
            if signature:
                moved += len(part)
    return moved
