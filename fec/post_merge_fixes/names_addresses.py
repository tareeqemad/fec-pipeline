"""Name and street-address fixes that need donor_key."""
import pandas as pd


def _recover_missing_streets(df: pd.DataFrame) -> int:
    """Fill a blank street from the donor's only street at the same city/state/ZIP."""
    keys = ['donor_key', 'contributor_city', 'contributor_state', 'contributor_zip']
    street = df['contributor_street_1'].fillna('').astype(str).str.strip()
    is_individual = df['entity_type'] == 'INDIVIDUAL'
    context = df[keys].fillna('').astype(str).apply(lambda column: column.str.strip())
    has_context = context.ne('').all(axis=1)

    source_mask = is_individual & has_context & street.ne('')
    sources = df.loc[source_mask, keys].copy()
    sources['contributor_street_1'] = street[source_mask]
    if sources.empty:
        return 0

    candidates = sources.groupby(keys)['contributor_street_1'].agg(
        lambda values: values.iloc[0] if values.nunique() == 1 else None
    )
    candidates = candidates.dropna().to_dict()

    n_recovered = 0
    target = is_individual & has_context & street.eq('')
    for idx in df.index[target]:
        key = tuple(df.at[idx, column] for column in keys)
        recovered = candidates.get(key)
        if recovered:
            df.at[idx, 'contributor_street_1'] = recovered
            n_recovered += 1
    return n_recovered


def _truncated_house_numbers(df: pd.DataFrame) -> int:
    """Z. '97 SHIRLEY RD'(1) -> '970 SHIRLEY RD'(103) for same donor."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        sts = grp['contributor_street_1'].dropna().value_counts()
        if len(sts) < 2:
            continue
        for a in sts.index:
            for b in sts.index:
                if a >= b:
                    continue
                pa, pb = str(a).split(' ', 1), str(b).split(' ', 1)
                if len(pa) < 2 or len(pb) < 2:
                    continue
                if pa[1] != pb[1]:
                    continue
                if not pa[0].isdigit() or not pb[0].isdigit():
                    continue
                if not (pa[0].startswith(pb[0]) or pb[0].startswith(pa[0])):
                    continue
                cnt_a, cnt_b = sts[a], sts[b]
                if cnt_a <= 2 and cnt_b >= 5:
                    mask = (df['donor_key'] == dk) & (df['contributor_street_1'] == a)
                    df.loc[mask, 'contributor_street_1'] = b
                    n_fixed += int(mask.sum())
                elif cnt_b <= 2 and cnt_a >= 5:
                    mask = (df['donor_key'] == dk) & (df['contributor_street_1'] == b)
                    df.loc[mask, 'contributor_street_1'] = a
                    n_fixed += int(mask.sum())
    return n_fixed
