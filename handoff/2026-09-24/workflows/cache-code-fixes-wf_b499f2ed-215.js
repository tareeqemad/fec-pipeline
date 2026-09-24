export const meta = {
  name: 'cache-code-fixes',
  description: 'Fix geocoding town search, reviewed points, house-number ZIPs and company-address publishing, each group reviewed',
  phases: [
    { title: 'Fix', detail: 'file-disjoint implementers (geo runs two stages)' },
    { title: 'Review', detail: 'independent reviewer per group' },
    { title: 'Fixup', detail: 'only where the reviewer requires it' },
  ],
}

const REPO = 'C:\\Users\\PC\\Desktop\\fec-pipeline'
const S = 'C:\\Users\\PC\\AppData\\Local\\Temp\\claude\\C--Users-PC-Desktop-fec-pipeline\\ed385f69-648b-4ef4-b621-6658f7a540a2\\scratchpad'
const AUDIT = S.replace('\\scratchpad', '\\tasks') + '\\w9a5q5pyq.output'

const COMMON = `
You fix the FEC pipeline at ${REPO} (Python; git main at 7ae5bb4f). THREE groups edit this working tree at the same time, each owning disjoint files; a separate read-only web-research job is also running (it writes only to the scratchpad). HARD RULES:
- Edit ONLY your group's files (below) plus NEW tests named tests/test_<group>_*.py. Anything else you need goes under 'needs_other_owner'.
- NEVER run pipeline entry scripts (clean.py, resolve.py, geocode.py, loader.py, sync_rosters.py, build_employers.py, pull.py), not even --help (clean.py has no argparse and starts a full run). Never write to data/ (no cache, CSV or rules edits: manual_employer_addresses.csv corrections are applied later by the lead from the research job — put any manual rows you want under 'manual_rows'). No git commit/stash/checkout/reset.
- Measure by calling library functions in memory on copies of the data/caches and write scratch output only under ${S}\\fix2\\<group>\\. Free public lookups (Nominatim at <=1 request/second with a proper User-Agent, Census geocoder) are allowed for measurement; NO Google or OpenAI calls.
- CSVs: pandas dtype=str, keep_default_na=False, na_values=[] ("NULL" is a real surname). Preserve line endings of files you edit.
- Never research private individuals' home addresses. Foreign addresses are never geocoded to the US and are kept as filed. Geocoding only adds coordinates, it never edits a cleaned address. Every fix evidence-based; a check you add must be able to fail (test it on the bad case); keep paid cache results (do not discard a paid Google point unless proven wrong).
The audit findings you work from (investigator + adversarial verifier per area; read your area IN FULL including the verifier's refutations, 'missed' and 'fix_risk'): ${AUDIT} (JSON result[i].finding / .verdict; areas: pobox-towns, street-wrong-town, employer-format-dupes, employer-accuracy). Scratch evidence of the audit: ${S}\\cacheaudit\\ (keys_final.csv etc.).
Tests: python -m pytest -q <your new tests> <existing tests of modules you touched>; report failures elsewhere without fixing them.
`

const GROUPS = [
  {
    key: 'geo',
    owns: 'fec/geocoding/** (engines.py, pipeline.py, reviewed_points.py, cache.py and the rest of the package), tests/test_geo_*.py, tests/test_zip_centroids_reference.py',
    stages: [
      `STAGE 1 — town-level search (area pobox-towns). engines.city_level sends free text 'CITY, ST ZIP, USA' with limit=1 and no type check, so PO-box/unique ZIPs unknown to OSM match POIs named '...USA' (Lego Miniland USA in Carlsbad for San Francisco 94141/94147, Murphy USA in Sealy for Austin 78711), counties (Jackson County MS, Jefferson Parish), roads or buildings named like the town (Crown Point, 'Potomac' in Towson), or the neighbouring city (North Little Rock for Little Rock 72217). Fix at the source: a structured search (city=, state=<full name>, country=us; postalcode only when the ZIP has a centroid; no ', USA' word; limit>1) that accepts only a SETTLEMENT (city/town/village/hamlet/suburb/borough/municipality...) whose NAME matches the filed city and that lies in the filed state (_valid_for_state); prefer the name-matching in-state settlement among the results (Houston MO vs TX; same-name places inside one state: MAGNOLIA TX, ENTERPRISE AL, POTOMAC MD — prefer the one consistent with the ZIP's neighbours or the filed ZIP when it has a centroid). In _fallback, when the ZIP has no centroid publish the town's settlement point. Add a one-time re-check: _needs_lookup returns True for cached 'nominatim_city' entries (no-centroid or blank ZIP, and also the centroid-ZIP ones the verifier flagged as the same flaw) that lack a new 'town_checked' flag; the new result is written with town_checked=True. Measure (free Nominatim, <=1 req/s) how many keys/rows would move and by how much on the current data and list the >8 km moves; keep a correct in-city point (Phoenix, Jamaica NY, Forest Hills NY: featureType=settlement may return nothing there — do not make those worse). Unit tests with mocked Nominatim answers (amenity 'Murphy USA', county 'Jackson County', road 'Crown Point', town 'North Little Rock' for LITTLE ROCK) must be rejected.`,
      `STAGE 2 — reviewed points and wrong street points (area street-wrong-town). In reviewed_points.py add (a) REVIEWED_POINTS {key: (lat, lng, note)} preferred by accepted_coordinates and skipped by _needs_lookup, for public committee/office addresses and donor streets whose correct point ALREADY sits in the cache under another (unused) key — copy those coordinates into the code now, because unused cache keys are about to be deleted: '5130 S FORT APACHE RD|LAS VEGAS|NV|89148', '8020 S RAINBOW BLVD|LAS VEGAS|NV|89139', '1636 N CEDAR CREST BLVD|ALLENTOWN|PA|18104', '3980 BROADWAY ST|BOULDER|CO|80304' (from the paid '3980 BROADWAY ST STE 103' entry), '3101 S OCEAN DR|HOLLYWOOD|FL|33019' and '2711 S OCEAN DR' (from '...|33019.0'), and every other case the auditor/verifier confirmed (list them all with the source key); (b) REVIEWED_WRONG_POINTS {key: (rejected_lat, rejected_lng, note)}: accepted_coordinates rejects the key, _needs_lookup queues it again, and results within 0.5 km of the rejected point are refused — for the confirmed wrong street points (Palm Beach S OCEAN BLVD 2335/2660/2770 in Manalapan, 750 PARK AVE NE Atlanta, 7972 CRANES PT WAY, and the others the verifier confirmed; not the refuted ones). A Census miss must fall back to the filed ZIP centroid (right town). Do NOT implement the refuted fixes (build_employers preserved-row coordinates 'fix 6', the ZIP-typo rule 'fix 9'). Also, from employer-format-dupes F9: strip a leading building name before geocoding (MAGELLAN '...101 E 2ND ST|TULSA'), the way _clean_street_for_geocoding strips suites. Add a free systematic re-check rule for old nominatim/google street entries far out in their ZIP (the auditor's 99 keys / 572 rows rule) where Google entries are replaced only when Census returns an in-ZIP point more than 1 km away; measure it. Tests for each list (they must fail on the old behaviour). Update tests/test_geo_street_zip_validation.py expectations if a reviewed list needs it.`,
    ],
  },
  {
    key: 'cleanzip',
    owns: 'fec/cleaning/addresses.py, fec/cleaning/pipeline/address_fixes/state_zip.py',
    stages: [
      `Area street-wrong-town, item 'ZIP was the house number' (4 rows; sub_ids 4010420231645702852, 4080620251215491506, 4062320251206918355, 4080120261540163141: KREBS, SACKHEIM, AGAM, JANIS). The filer put the house number in the ZIP box (raw ZIP = house number + '0001') and the real city + ZIP inside street_1 (JANIS: street_2 'POTO 2085'); the pipeline derived a fake city/state from that ZIP and pinned the donor in another state (SACKHEIM in Albany NY although she lives in Kamas UT). Fix it in cleaning: detect raw ZIP == house number + '0001' (or 5 digits equal to the leading house number) together with '<CITY> <5-digit ZIP>' at the end of street_1 (or the obvious street_2 variant), move the city/ZIP out of street_1 into city/ZIP, and take the state from the donor's own other filings (same person) or from the ZIP's state; never take an address from another person. Verify each of the 4 against raw filings (do not web-research the homes). Tests that fail on the old behaviour; measure on the data by replaying the step(s) in memory (python ${S}\\replay_clean.py <outdir> runs the whole clean stage into a scratch dir in ~9 min, never into data/).`,
    ],
  },
  {
    key: 'employers',
    owns: 'build_employers.py, fec/resolve/pipeline/locations.py, fec/resolve/pipeline/apply.py, fec/config/streets.py, tests/test_geo_employer_address_normalization.py, tests/test_build_employer_locations.py, tests/test_employer_locations.py',
    stages: [
      `Areas employer-format-dupes and employer-accuracy (read both in full, including the verifiers' refutations). Implement:
(1) F1: build_employers publishes nothing for 67 employers although resolve's apply step (publishable lookup) already picks a grounded address — make build_employers use the same publishable-first lookup as apply (then fall back), and add a state guard: a grounded address whose state is not among the employer's donor states goes to a review list instead of being published (the verifier found WILLIAMS & CONNOLLY stale and BOULEVARD HEALTHCARE uncertain among the 17 out-of-state ones). Measure: how many employers gain a published office.
(2) Carried-over rows: build_employers re-publishes 32 rows from the previous CSV that have no cache or manual source, keeping their old 'verified/grounded' trust (at least 9 proven wrong). Stop carrying trust for rows with no current source (address_trust '' = not published). Their correct replacements come from the research job as manual rows, applied later by the lead.
(3) HOME OFFICES (owner decision: publish city/state/ZIP only): when a published company office equals the filing address of a donor who works there (street without unit + ZIP5), has no suite/unit, at most 2 filers at that address and the employer has at most 3 donors — a home-based business — publish the company's city, state and ZIP but not the street, whatever the source (AI or manual; e.g. the manual HSK CONSULTING row). Measure how many rows (auditor: ~144; ~77 residential-looking, ~67 storefronts whose owner files the business address — decide with the criteria, and list both groups so the lead can review; storefronts with a unit/suite or several employees keep their street).
(4) F5 unit format: company street text in USPS form like donor streets — '10TH FLOOR'/'SEVENTH FLOOR'/'FLOOR 24' -> 'FL n', 'SUITE n' / 'SUITE #n' -> 'STE n' (use a shared rule in fec/config/streets.py so donor and employer text agree; the owner already decided employer addresses follow USPS style). The current tests pin '200 LIBERTY ST 6TH FLOOR' and '175 STRAFFORD AVE SUITE ONE' — update them to the USPS form (spelled unit words: decide consistently and document). Measure: 314 long-form DB rows -> 0; check donor street_2 effects (16+3 values) are right.
(5) F7: build_employers looks up coordinates under the raw key, the normalised key and the donor-form key without the unit, keeping the best accepted_coordinates level (30 offices move from a ZIP/city centroid to an existing street point for free, 12 of them already-paid Google points). Do NOT implement the refuted 'fix 6'.
Do NOT change: the tested exact-ai_not_found rule (F2) — report it; F6 (employer street_2 schema change) — report it; the gpt5/xai publishing policy — those rows are being researched and corrected via manual rows. List under 'manual_rows' any manual_employer_addresses.csv rows you want the lead to add (with source URL), e.g. F4 re-keys (PAUL WEISS, PROGRESSIVE, UNITEX, not RADCO/FELSON) and F9 (MASSMUTUAL, US ARMY public offices), CONGRESS AUTO INS (5 Whittier St, 4th Floor, Framingham) if you verify them.`,
    ],
  },
]

const IMPL = {
  type: 'object',
  properties: {
    group: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    tests_added: { type: 'array', items: { type: 'string' } },
    tests_result: { type: 'string' },
    changes: { type: 'array', items: { type: 'string' } },
    measured_effect: { type: 'string' },
    manual_rows: { type: 'array', items: { type: 'string' }, description: 'CSV lines for manual_employer_addresses.csv (name,address,city,state,zip,is_primary,note,source_name,source_url) with evidence' },
    not_done: { type: 'array', items: { type: 'string' } },
    needs_other_owner: { type: 'array', items: { type: 'string' } },
    open_doubts: { type: 'array', items: { type: 'string' } },
  },
  required: ['group', 'files_changed', 'tests_added', 'tests_result', 'changes', 'measured_effect', 'manual_rows', 'not_done', 'needs_other_owner', 'open_doubts'],
}
const REVIEW = {
  type: 'object',
  properties: {
    group: { type: 'string' },
    verdict: { type: 'string', enum: ['approve', 'approve_with_notes', 'must_fix'] },
    must_fix: { type: 'array', items: { type: 'string' } },
    notes: { type: 'array', items: { type: 'string' } },
    measured: { type: 'string' },
  },
  required: ['group', 'verdict', 'must_fix', 'notes', 'measured'],
}

const out = await pipeline(
  GROUPS,
  async (g) => {
    let prev = null
    for (let i = 0; i < g.stages.length; i++) {
      const r = await agent(`${COMMON}\n\nYOUR GROUP: '${g.key}'. You own: ${g.owns}.\n${prev ? 'Earlier stage of your group (already in the tree):\n' + JSON.stringify(prev) + '\n' : ''}\nTASK:\n${g.stages[i]}`,
        { label: `fix:${g.key}${g.stages.length > 1 ? ':' + (i + 1) : ''}`, phase: 'Fix', schema: IMPL })
      if (!r) break
      prev = prev ? { stage1: prev, ['stage' + (i + 1)]: r } : r
    }
    return prev
  },
  (impl, g) => impl && agent(`${COMMON}\n\nINDEPENDENT REVIEWER for group '${g.key}' (owns ${g.owns}); do not edit files except scratch. Task was:\n${g.stages.join('\n\n')}\n\nImplementer report(s):\n${JSON.stringify(impl)}\n\nRead the diff (git diff -- <owned files>) and new tests, re-measure the claimed effects yourself, check owner rules (no paid lookups discarded without proof, foreign untouched, geocoding never edits addresses, home offices publish city/state/ZIP only, no home research), check tests fail on the old behaviour, check nothing unrelated changes. must_fix only for real defects with evidence.`,
    { label: `review:${g.key}`, phase: 'Review', schema: REVIEW }).then(rev => ({ impl, rev })),
  (pair, g) => {
    if (!pair || !pair.rev || pair.rev.verdict !== 'must_fix' || !pair.rev.must_fix.length) return pair
    return agent(`${COMMON}\n\nYOUR GROUP: '${g.key}' (owns ${g.owns}). Your changes:\n${JSON.stringify(pair.impl)}\n\nReviewer must-fix items:\n${JSON.stringify(pair.rev.must_fix)}\nNotes: ${JSON.stringify(pair.rev.notes)}\n\nVerify each (it may be wrong; explain with evidence), fix the real ones, rerun tests, re-measure, and return the full updated result.`,
      { label: `fixup:${g.key}`, phase: 'Fixup', schema: IMPL }).then(fix => ({ ...pair, fixup: fix }))
  },
)
return out.filter(Boolean)
