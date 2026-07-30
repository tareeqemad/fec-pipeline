"""Name and street-address fixes that need donor_key."""
import pandas as pd


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
