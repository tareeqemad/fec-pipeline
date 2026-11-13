# Notes for Claude sessions on this project

The owner writes in Arabic. Read this file before changing anything.

## The owner's rules

- Reply in Arabic: the outcome first, short tables, no em-dashes or arrows. The last message must stand alone.
- Work on a branch. Merge into `main` only after the owner says "ادمج", with `git merge --no-ff`, then push `main`.
  Never open a pull request unless asked.
- Never put a key in chat or in the repository. Keys live in `.env`, which is not committed.
- **Never merge two people unless it is proven.** A wrong merge is worse than an open filing. When the owner of a
  filing is in doubt, the filing is held (`identity_status` `held`) and counts toward nobody.
- **Never change what a donor filed as their occupation.** The occupation is only given a category.
- Every reviewed decision is a row in `data/rules/`, with where it comes from in its `source` column.
- No word list and no table of patterns inside the code. They belong in `data/config/` or `data/rules/`.
- Simple code: no clever tricks, no added layers, no dense one-liners. Clear function and variable names.
- No AI model in the pipeline. Occupation categories come from a reviewed table.

## How the project runs

Each command at the root only calls `main` in its stage's `pipeline.py`. Each `pipeline.py` lists its steps, in
order, in its docstring: start reading there.

| Order | Command | Stage | Reads | Writes |
| --- | --- | --- | --- | --- |
| 1 | `python pull.py C00797670 --period 2026` | `fec/pull` | FEC API | `data/raw/contributions.csv` (appends new filings only) |
| 2 | `python clean.py` | `fec/cleaning`, `fec/matching` | the raw file, `data/rules`, `data/config` | `data/output/contributions_cleaned.csv`, `data/cache/donor_keys.csv` |
| 3 | `python geocode.py` | `fec/geocoding` | the cleaned file, `data/cache/geocode_cache.json` | latitude and longitude columns |
| 4 | `python resolve.py --apply` | `fec/resolve` | the cleaned file | `employer_status`, previous employers |
| 5 | `python loader.py --reset` | `fec/database` | the cleaned file, `data/reference` | PostgreSQL, rebuilt from scratch in one transaction |

`resolve.py` refuses a file it already resolved: run `clean.py` again first.

Names: `read_` reads a file, `load_` writes to the database, `apply_` applies reviewed rules, `clean_` is a
cleaning step. A name starting with `_` stays inside its file.

## Checking a change

- `ruff check . && ruff format --check .` and `python -m pytest -q tests` must pass.
- Run steps 2 to 4 and compare `data/output/contributions_cleaned.csv` with the one on `main`. Every difference
  must be explained to the owner before a merge. Unexplained differences mean the change is not done.
- For the loader, compare the database content by what each row names (donor key, address, company name), not by
  internal IDs: the IDs change whenever rows are inserted in another order.
- Before a real `clean.py` run that will be compared, keep `data/cache/donor_keys.csv` as it is on `main`; it
  keeps donor keys stable between runs.

## State on 2026-11-11

Lines of Python on 2026-11-13: cleaning stage (`fec/cleaning`, `fec/matching`, `fec/vocabulary`) 3,997, under
the owner's target of 4,000; database 928; geocoding 378; common 335; pull 328; resolve 139. The cut from 5,623
changed no output: word lists moved to `data/config`, the duplicate review report (read by nothing) was removed.
Options still measured but not done: turn the typo learning into a reviewed table (about 180 lines), turn nine
small steps into rows of `filing_corrections.csv` (about 170 lines). Both stop fixing new filings automatically.

A fresh clone of `main` runs steps 2 to 5 and reproduces the committed output exactly (222,603 filings, 20,013
donor groups, 67 tests).

### First real run on the owner's machine: what to watch

1. **`pull.py` has not run against the real FEC since it was simplified.** It was checked by tests and by comparing
   old and new code on faked answers only. On the first real pull, check that it appends only new filings, that
   a second run adds nothing, and that `data/raw/fec_source_fields.csv` grows with it.
2. The raw data is behind the FEC: for example four joint MEYERS filings of September and October 2025 (reported
   in an amended report of March 2026) are not in `data/raw`. A fresh pull will bring such filings.
3. New filings bring new texts. After `clean.py`, read `data/reports/new_occupations.csv` (occupations not yet in
   `data/rules/occupation_categories.csv`) and the warnings listed in the README.
4. `geocode.py` looks up only addresses not in the cache; the public Nominatim server allows one request per
   15 seconds, so many new addresses take long. The cache holds paid lookups: never delete it.
5. The README asks for Python 3.12 and PostgreSQL 18; this work was run with Python 3.13 and PostgreSQL 16. The
   schema needs PostgreSQL 15 or later (`UNIQUE NULLS NOT DISTINCT`).
6. Internal database IDs change on every load. Anything that points at a donor must use `donor_key`.

### The 52 open filings

50 filings are held and 2 are unresolved, on purpose. They were researched again on 2026-11-11 and nothing
proves an owner, so nothing was changed. The rows are in `data/rules/donor_identity_rules.csv` (`hold`).

| Case | Filings | Why it is open |
| --- | --- | --- |
| Joint filings naming two people: MEYERS, MORRIS, BRAVERMAN, ZELDIN, SPELLMAN, SANDERSON, TAUBER, TISHKOFF | 35 | The filing does not say whose money it is. The full FEC record does not either: these are earmarked gifts and FEC carries no running total for them. Earlier decisions by "first-named person" were withdrawn at the owner's request (commit 316767d) |
| GOLDSTEIN, RICHARD, retired, 200 SE Mizner Blvd, Boca Raton | 12 | Probably the Nixon Peabody attorney: his Boca filings stop in 2024-01 and these start in 2024-10 in the same ZIP, three of the same candidate committees, and he files as retired from Chevy Chase in 2025. Not proven: the name is common in Boca Raton. The closest case to proof, if the owner wants to restore one |
| HEALTH, GOOD (Good Health Inc address) | 2 | A company name filed as a person. Eight payments of $1,875 came from that address in 2022 under several names, which suggests one pledge of the CEO, without proof |
| VIOLIN, JON | 1 | Filed once from the address of Joan and George Violin; maybe a slip for JOAN, not proven |
| SEATTLE POLITICAL NETWORK | 2 | No public trace of the name was found |

Do not look up private people's home addresses to settle these; use the filings themselves and professional or
official pages.

### Decisions worth knowing

- Occupation categories: `data/rules/occupation_categories.csv` (2,976 texts, all read by hand on 2026-11-11, 83
  corrected). A text not in it takes the category of its last word listed in
  `data/config/occupation_category_words.csv`, else OTHER, and is listed in the new-occupations report.
- The work fields are read once, after the entity type is known.
- Roster people (`leaders.csv`, `key_accomplices.csv`) are read as rows shaped like filings and go through the
  same loaders as the filings.
- The Nominatim settings (`NOMINATIM_URL`, `NOMINATIM_USER_AGENT`, `NOMINATIM_DELAY_SECONDS`) are optional and
  documented in the README.

### Company names (2026-11-13)

- An `&` in a company name is written `AND`, in filed names and in reviewed names alike, so one firm has one
  spelling (KIRKLAND AND ELLIS LLP). A reviewed name with a glued `&` keeps it (S&P GLOBAL). The website and the
  chatbot treat `&` and `AND` as the same word when they search.
- `employer_name_rules.csv` has two actions. `rename` (the older rows) also says the text is a company.
  `spelling` (937 rows added 2026-11-13) only joins spellings of one firm that differ by a legal form, spacing,
  punctuation, a short form or a typo (GOOGLE LLC, GOOGLE; NYULANGONE, NYU LANGONE; RAYMKND JAMES, RAYMOND JAMES).
  Names one letter apart that can be two organisations stay apart (AES and ARES MANAGEMENT, STAMFORD and STANFORD). Spelling rows are not evidence that
  a text names a company, so they do not change how the work fields are read.
- Kept apart on purpose: generic names that different organisations share (LEGAL AID SOCIETY, EAR NOSE THROAT
  CENTER, COMPASS, BAIN, FORBES, HEALTH FIRST and 50 more), and R-PAC and ADVOCATE BROKERAGE, whose legal forms
  the donor identity review of 2026-10-24 kept separate. The list of the 56 names still waiting is not in the
  repository; add a `spelling` row only after reading who files each spelling.
- `data/reference/employer_locations.csv` follows the reviewed names: when spellings join, their office rows join,
  one row per address, and the most trusted address is the primary one.
- Effect on the output, measured against `main`: 6,261 employer and 374 previous-employer values take the joined
  spelling (11,004 distinct employers become 10,201). 35 occupations of 7 donors change through
  `fill_occupation_from_donor`, which fills an empty or SELF-EMPLOYED occupation only when the donor has one role at
  that employer: joined spellings can add or remove that one role. Three organisations whose own name was joined
  get a new donor key. No person was merged or split; OCHSTEIN's two keys, already joined by a reviewed
  `merge_keys` row, are now joined by matching itself.
