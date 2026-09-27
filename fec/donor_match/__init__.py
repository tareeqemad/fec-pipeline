"""Donor identity resolution: decide who is who, then make each identity consistent.

The flow (clean.py -> identify_donors -> here):

    matcher.match_donors
        build_profiles (matcher)      one profile per name/location/suffix
        five pair phases (phases)     candidate pairs -> compute_score (scoring)
        chains + force merges (matcher)
        -> donor_key per cluster
    keys.apply_donor_key + verified identity rules
    canonicalize (+ name_choice)      one official name per donor
    canonical_employers               one employer name per donor
    canonical_addresses               one street/unit/PO Box per donor
    dedup_review.build_donor_dedup_review  detect-only report for human triage

constants.py holds matching weights and nickname rules. Every human identity
decision lives in data/rules/donor_identity_rules.csv."""
