"""Check every identity case reviewed on 2026-09-24 against the cleaned CSV; exit 1 on any failure.

Usage: python tools/audits/identity_cases.py [path/to/contributions_cleaned.csv]
"""
import sys

import pandas as pd

path = sys.argv[1] if len(sys.argv) > 1 else "data/contributions_cleaned.csv"
b = pd.read_csv(path, dtype=str, keep_default_na=False)
b['amt'] = pd.to_numeric(b.contribution_receipt_amount)
by = b.set_index('sub_id')
fails = []

def key(sub): return by.at[sub, 'donor_key']
def total(sub): return b.loc[b.donor_key == key(sub), 'amt'].sum()
def check(label, ok, detail=''):
    print(('OK  ' if ok else 'FAIL') + f'  {label}  {detail}')
    if not ok: fails.append(label)
def apart(label, a, c):
    check(label, key(a) != key(c), f'{key(a)} vs {key(c)}')
def names(n): return b[b.contributor_name == n]
def keys_of(n): return set(names(n).donor_key)

# separations
apart('Rosenberg doctor vs lawyer', '4122920221645227694', names('ROSENBERG, ANDREW')[names('ROSENBERG, ANDREW').contributor_city == 'HAWORTH'].sub_id.iloc[0])
check('Weinberg Debra/Debbi: 6 separate groups', len(set(b[b.contributor_name.str.match(r'WEINBERG, DEB(RA|BI)$')].donor_key)) == 6)
check('Levy Richard: Highland Park vs Wilmette apart', len(keys_of('LEVY, RICHARD')) >= 2)
check('Franco Joseph: Brooklyn vs Great Neck apart', len(set(b[b.contributor_name.str.match(r'FRANCO, (JOSEPH|JOE)$')].donor_key)) >= 2)
check('Goldstein Jeffrey: North Andover vs Newton apart', len(keys_of('GOLDSTEIN, JEFFREY')) >= 2)
g = names('GOLDSTEIN, RICHARD')
gk = g.groupby('donor_key').amt.sum()
check('Richard Goldstein: Hangley $525 alone', round(gk.get(key('4011620261302587184'), 0)) == 525, str(gk.to_dict()))
check('Richard Goldstein: Nixon $30,950', round(total('4110120231808001460')) == 30950)
check('Richard Goldstein: Mizner $10,250 held apart', round(total('4011620261302592540')) == 10250)
check('Brodie: lawyer $20,110', round(b.loc[b.donor_key == names('BRODIE, STEVEN').donor_key.iloc[0], 'amt'].sum()) == 20110)
check('Brodie: $7,000 alone', round(total('4030420261386075351')) == 7000)
check('Small: New York $1,000 alone', round(total('4052620261511336804')) == 1000)
check('Goldman: VeArc $200 vs Inflation $250', round(total('4011420251130090187')) == 200 and round(total('4031920261424760196')) == 250)
check('Diaz: Trident $350 alone', round(total('4092520231802907431')) == 350)
check('Rubin: $2,000 held', round(total('4072420241978929001')) == 2000)
# network filings
net = b[b.contributor_name.str.contains('POLITICAL NETWORK')]
check('Network filings: each its own key', net.donor_key.nunique() == len(net), f'{len(net)} filings')
check('De Toledo Philip $1,889,300', round(b.loc[b.contributor_name == 'DE TOLEDO, PHILIP', 'amt'].sum()) == 1889300)
check('Comanor $17,300', round(b.loc[b.contributor_name == 'COMANOR, WILLIAM', 'amt'].sum()) == 17300)
# holds / joint names
check('Morris STUN $2,000 off Ellen', key('4052620261511336845') not in set(names('MORRIS, ELLEN').donor_key) or round(total('4052620261511336845')) == 2000)
check('Ellen Morris $12,000', round(b.loc[b.contributor_name == 'MORRIS, ELLEN', 'amt'].groupby(b.donor_key).sum().max()) == 12000)
for sub, solo, amt in [('4062420241962023287', 'BOSCHAN, SHIRA', 3250), ('4112020241071383819', 'STEIN, BERNARDO', 1050),
                       ('4062420241962023289', 'SANDERSON, STEVEN', 6500), ('4012120261303039427', 'TAUBER, KENNETH', 3450)]:
    solo_total = round(b.loc[b.donor_key.isin(keys_of(solo)) & (b.donor_key != key(sub)), 'amt'].sum())
    check(f'{solo}: joint filing held, solo total {amt}', key(sub) not in keys_of(solo) and solo_total == amt, f'solo={solo_total}')
check('Boschan solo filings keep SHIRA', set(names('BOSCHAN, SHIRA').contributor_first_name) == {'SHIRA'})
# people fixes
check('Kellogg: HEALTH, GOOD not Sarah', by.at['4011420231698184106', 'contributor_name'] == 'HEALTH, GOOD')
check('Tuchin: Hackman details cleared', by.at['4080220231759311394', 'contributor_employer'] == '')
check('Harris Michael: no Permanente', 'PERMANENTE' not in ' '.join(b.loc[b.donor_key == key('4112020241071382260'), 'previous_employer']))
check('Orientale: employer cleared', by.at['4052120241949843855', 'contributor_employer'] == '')
check('Foldes $100: SELF-EMPLOYED kept', by.at['4102420231807010022', 'contributor_employer'] == 'SELF-EMPLOYED')
check('Chrystal: no garbled name', not b.contributor_name.str.contains('GLENN STUART CHRYSTA').any())
check('S. Wolf Harris name kept', 'WOLF' in by.at['4081920251218698011', 'contributor_name'])
check('T Alison Robbins name kept', 'ALISON' in by.at['4111420241068713232', 'contributor_name'])
print('\n', 'ALL OK' if not fails else f'{len(fails)} FAILED: {fails}')
sys.exit(1 if fails else 0)
