"""Self-employed donors: the own firm is the workplace, and 'SELF-EMPLOYED' is not an occupation."""
import pandas as pd

from fec.cleaning.donor_consistency.employer import _own_firm_absorbs_self_employed
from fec.cleaning.donor_consistency.occupation import _fill_self_employed_occupation_from_donor

_COLS = ['entity_type', 'donor_key', 'contributor_name', 'contributor_employer', 'contributor_occupation', 'occupation_category']


def _df(rows):
    return pd.DataFrame(rows, columns=_COLS)


def test_own_surname_firm_absorbs_the_donors_self_employed_rows():
    df = _df([('INDIVIDUAL', 'k1', 'FANE, SCOTT', 'SELF-EMPLOYED', 'CPA', 'ACCOUNTING / TAX')] * 3
             + [('INDIVIDUAL', 'k1', 'FANE, SCOTT', 'SCOTT FANE CPA PA', 'CPA', 'ACCOUNTING / TAX')])
    assert _own_firm_absorbs_self_employed(df) == 3
    assert set(df.contributor_employer) == {'SCOTT FANE CPA PA'}


def test_a_joint_personal_name_or_another_persons_firm_does_not_absorb():
    df = _df([('INDIVIDUAL', 'k1', 'WEISZ, GEORGE', 'SELF-EMPLOYED', 'INVESTOR', 'FINANCE / INVESTMENT')] * 2
             + [('INDIVIDUAL', 'k1', 'WEISZ, GEORGE', 'GEORGE AND LEESA WEISZ', 'INVESTOR', 'FINANCE / INVESTMENT')]
             + [('INDIVIDUAL', 'k2', 'COHEN, DAN', 'SELF-EMPLOYED', 'ATTORNEY', 'LEGAL')]
             + [('INDIVIDUAL', 'k2', 'COHEN, DAN', 'SCHALL LAW FIRM', 'ATTORNEY', 'LEGAL')]      # not his surname
             + [('INDIVIDUAL', 'k3', 'LEE, AMY', 'SELF-EMPLOYED', 'DENTIST', 'MEDICAL / HEALTHCARE')]
             + [('INDIVIDUAL', 'k3', 'LEE, AMY', 'LEE DENTAL PC', 'DENTIST', 'MEDICAL / HEALTHCARE')]
             + [('INDIVIDUAL', 'k3', 'LEE, AMY', 'LEE ORTHODONTICS LLC', 'DENTIST', 'MEDICAL / HEALTHCARE')])  # two firms: ambiguous
    assert _own_firm_absorbs_self_employed(df) == 0


def test_self_employed_occupation_takes_the_donors_real_one():
    df = _df([('INDIVIDUAL', 'k1', 'AGAM, JERRY', 'SELF-EMPLOYED', 'SELF-EMPLOYED', 'SELF-EMPLOYED')] * 2
             + [('INDIVIDUAL', 'k1', 'AGAM, JERRY', 'SELF-EMPLOYED', 'REAL ESTATE', 'REAL ESTATE')] * 3
             + [('INDIVIDUAL', 'k2', 'AHRAM, JOSEPH', 'JOSEPH AHRAM MD', 'SELF-EMPLOYED', 'SELF-EMPLOYED')]
             + [('INDIVIDUAL', 'k2', 'AHRAM, JOSEPH', 'REX HEALTHCARE', 'PHYSICIAN', 'MEDICAL / HEALTHCARE')]
             + [('INDIVIDUAL', 'k3', 'LOPES, JW', 'JW LOPES LLC', 'SELF-EMPLOYED', 'SELF-EMPLOYED')] * 4)
    assert _fill_self_employed_occupation_from_donor(df) == 3
    assert df[df.donor_key == 'k1'].contributor_occupation.tolist() == ['REAL ESTATE'] * 5
    assert df[df.donor_key == 'k1'].occupation_category.tolist() == ['REAL ESTATE'] * 5
    assert df[df.donor_key == 'k2'].contributor_occupation.tolist() == ['PHYSICIAN', 'PHYSICIAN']   # from another employer
    assert set(df[df.donor_key == 'k3'].contributor_occupation) == {'SELF-EMPLOYED'}            # nothing better known
