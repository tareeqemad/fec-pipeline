"""Keep filed self-employment separate from a named firm in another filing."""
import pandas as pd

from fec.cleaning.donors.occupation import _fill_self_employed_occupation_from_donor
from fec.cleaning.donors.steps import apply_donor_consistency
from fec.cleaning.reports.trail import AuditTrail

_COLS = ['entity_type', 'donor_key', 'contributor_name', 'contributor_employer', 'contributor_occupation', 'occupation_category']


def _df(rows):
    df = pd.DataFrame(rows, columns=_COLS)
    df['sub_id'] = [f'test-self-{index}' for index in df.index]
    df['contributor_street_1'] = '10 MAIN ST'
    df['contributor_street_2'] = ''
    df['contributor_city'] = 'NEW YORK'
    df['contributor_state'] = 'NY'
    df['contributor_zip'] = '10001'
    df['contribution_receipt_date'] = '2024-01-01'
    df['occupation_status'] = 'DISCLOSED'
    return df


def test_own_surname_firm_does_not_replace_filed_self_employment():
    df = _df([('INDIVIDUAL', 'k1', 'FANE, SCOTT', 'SELF-EMPLOYED', 'CPA', 'ACCOUNTING / TAX')] * 3
             + [('INDIVIDUAL', 'k1', 'FANE, SCOTT', 'SCOTT FANE CPA PA', 'CPA', 'ACCOUNTING / TAX')])
    apply_donor_consistency(df, AuditTrail())
    assert df.contributor_employer.tolist() == ['SELF-EMPLOYED'] * 3 + ['SCOTT FANE CPA PA']


def test_a_joint_personal_name_or_another_persons_firm_does_not_absorb():
    df = _df([('INDIVIDUAL', 'k1', 'WEISZ, GEORGE', 'SELF-EMPLOYED', 'INVESTOR', 'FINANCE / INVESTMENT')] * 2
             + [('INDIVIDUAL', 'k1', 'WEISZ, GEORGE', 'GEORGE AND LEESA WEISZ', 'INVESTOR', 'FINANCE / INVESTMENT')]
             + [('INDIVIDUAL', 'k2', 'COHEN, DAN', 'SELF-EMPLOYED', 'ATTORNEY', 'LEGAL')]
             + [('INDIVIDUAL', 'k2', 'COHEN, DAN', 'SCHALL LAW FIRM', 'ATTORNEY', 'LEGAL')]      # not his surname
             + [('INDIVIDUAL', 'k3', 'LEE, AMY', 'SELF-EMPLOYED', 'DENTIST', 'MEDICAL / HEALTHCARE')]
             + [('INDIVIDUAL', 'k3', 'LEE, AMY', 'LEE DENTAL PC', 'DENTIST', 'MEDICAL / HEALTHCARE')]
             + [('INDIVIDUAL', 'k3', 'LEE, AMY', 'LEE ORTHODONTICS LLC', 'DENTIST', 'MEDICAL / HEALTHCARE')])  # two firms: ambiguous
    self_employed = df.contributor_employer.eq('SELF-EMPLOYED')
    apply_donor_consistency(df, AuditTrail())
    assert df.loc[self_employed, 'contributor_employer'].eq('SELF-EMPLOYED').all()


def test_a_self_employed_filing_in_another_job_keeps_its_employer():
    df = _df([('INDIVIDUAL', 'k1', 'FOLDES, NADINE', 'EVENSKY & KATZ / FOLDES WEALTH MANAGEMENT', 'WEALTH MANAGER', 'FINANCE / INVESTMENT'),
              ('INDIVIDUAL', 'k1', 'FOLDES, NADINE', 'SELF-EMPLOYED', 'SOCIAL WORKER', 'SOCIAL SERVICES')])
    apply_donor_consistency(df, AuditTrail())
    assert df.contributor_employer.tolist()[1] == 'SELF-EMPLOYED'


def test_a_title_at_the_firm_does_not_assign_it_to_self_employed_filings():
    df = _df([('INDIVIDUAL', 'k1', 'BAYER, JEFFREY', 'BAYER VENTURES LLC', 'PRESIDENT & CEO', 'EXECUTIVE / C-SUITE'),
              ('INDIVIDUAL', 'k1', 'BAYER, JEFFREY', 'SELF-EMPLOYED', 'COMMERCIAL REAL ESTATE', 'REAL ESTATE'),
              ('INDIVIDUAL', 'k2', 'STUTMAN, ROBERT', 'STUTMAN LAW', 'ATTORNEY', 'LEGAL'),
              ('INDIVIDUAL', 'k2', 'STUTMAN, ROBERT', 'SELF-EMPLOYED', 'SELF', 'OTHER')])
    apply_donor_consistency(df, AuditTrail())
    assert df.loc[[1, 3], 'contributor_employer'].tolist() == ['SELF-EMPLOYED'] * 2


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
