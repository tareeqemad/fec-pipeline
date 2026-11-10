"""Apply record cleaning rules in order."""
import pandas as pd

from fec.cleaning.entities import normalize_business_names
from fec.cleaning.rules.occupation_context import _disambiguate_vague_occupation
from fec.cleaning.work.read import read_work

def apply_record_rules(df: pd.DataFrame, filed_employers: pd.Series, filed_occupations: pd.Series) -> pd.DataFrame:
    """Settle names, then read every individual's work fields from the filing."""
    df['is_individual'] = (df['entity_type'] == 'INDIVIDUAL')
    normalize_business_names(df)
    read_work(df, df['is_individual'], filed_employers, filed_occupations)
    df.loc[df['entity_type'].eq('COMMITTEE/PAC') & df['occupation_category'].isna(), 'occupation_category'] = 'POLITICAL COMMITTEE'
    _disambiguate_vague_occupation(df)
    return df
