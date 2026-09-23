# Audits (run from the repo root after the pipeline, `PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/audits/<script>.py`)

| Script | What it proves |
|---|---|
| `final_verify.py` | rows and sub_ids match raw, quality gates, audit totals, key donors, roster drift (`sync_rosters.py --check`), coordinates, employer-address coverage |
| `address_audit.py` | every cleaned street/city/state/ZIP is grounded in the SAME donor's raw filings; lists donors whose distinct raw addresses or apartment numbers were reduced (`data/_review/address_audit_*.csv`) |
| `name_audit.py` | no row carries a surname/first name its donor never filed (nickname, initial, typo and suffix variants allowed); donor keys holding non-variant raw names (`data/_review/name_audit_*.csv`) |
| `self_employed_audit.py` | raw self-employed filings keep their real occupation, companies never collapse into SELF-EMPLOYED, own-firm/self-employed splits, `previous_employer` never set on self-employed rows |

Read the HISTORY-step flags of the address audit in full: they are few and each must be explainable (FEC.gov same-person recovery, verified rule, manual override, ZIP-vs-state fix).
