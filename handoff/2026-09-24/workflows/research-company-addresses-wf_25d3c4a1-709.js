export const meta = {
  name: 'research-company-addresses',
  description: 'Web-research 617 published company addresses, correct wrong ones with sources, independently re-check every change',
  phases: [
    { title: 'Research', detail: '16 researchers, ~39 companies each: verify or correct the published office with a public source' },
    { title: 'Check', detail: 'an independent checker per batch re-verifies every correction and a sample of confirmations' },
  ],
}

const S = 'C:\\Users\\PC\\AppData\\Local\\Temp\\claude\\C--Users-PC-Desktop-fec-pipeline\\ed385f69-648b-4ef4-b621-6658f7a540a2\\scratchpad'
const LIST = S + '\\cacheaudit\\research_targets.csv'
const N = 617
const BATCH = 39
const batches = []
for (let start = 1; start <= N; start += BATCH) batches.push({ from: start, to: Math.min(N, start + BATCH - 1) })

const RULES = `
You research PUBLIC company office addresses for an FEC donor dashboard (project C:\\Users\\PC\\Desktop\\fec-pipeline). Read-only: do not edit any project file; write scratch files only under ${S}\\research\\.
Input: ${LIST} (CSV, read with pandas dtype=str, keep_default_na=False). Columns: rid, employer_name (the company as it appears in our data), employer_address/city/state/zip (what we publish now), is_primary, address_trust, stratum (A_legacy_search = old AI web answer with no saved source; D_gpt5_search / E_xai_search = AI answers; G_preserved_nocache = a row carried over from an old build with no source), method, src_url, n_donors, n_filings, occupations, donor_places, donor_states, donor_names (people who file this employer; use them ONLY to confirm which organisation they work for, e.g. a LinkedIn/firm bio page), prior_verdict/prior_evidence (an earlier spot check, may be wrong).
For each row decide which organisation the donors actually work for (the name can be ambiguous: 'BOMA' for a Los Angeles director is BOMA Greater Los Angeles, not BOMA International; 'MPI' or 'AREAS' may be different companies) and find that organisation's public office address that best fits the donors (same state/metro office when the company has one there; otherwise the headquarters). Use official websites, state business registries, SEC filings, reputable directories. Verdicts:
- correct: the published address is the right organisation's current office (small suite differences acceptable -> give the exact current form as corrected fields anyway if you have it).
- corrected: the published address is wrong/outdated/wrong company; give the right office with a source URL.
- home_based: the business is run from a person's home (sole proprietor / tiny firm; the published address looks residential or is the donor's own filing address). DO NOT research the home or any person's residence. Give only city/state/zip of the business if publicly stated, no street.
- generic_no_office: the 'employer' is not one organisation with a meaningful office (FEDERAL GOVERNMENT, US ARMY as a whole, 'STATE OF X', 'SELF', a sector word) -> publish no address.
- not_found: no reliable public office can be found for the right organisation -> publish no address (INVALID).
- ambiguous: several organisations fit and the evidence cannot decide -> publish no address; say which candidates.
Never invent or guess an address; every corrected/correct row needs a source URL you actually opened. Never research private individuals' home addresses. Keep the donor names out of the note text.
Address style: street as the organisation writes it (the pipeline normalises to USPS style later), suite in the street field ('..., Suite 400'), 5-digit ZIP.
`

const ROWS = {
  type: 'object',
  properties: {
    rows: { type: 'array', items: { type: 'object', properties: {
      rid: { type: 'integer' },
      employer_name: { type: 'string' },
      verdict: { type: 'string', enum: ['correct', 'corrected', 'home_based', 'generic_no_office', 'not_found', 'ambiguous'] },
      organisation: { type: 'string', description: 'the organisation the donors work for' },
      address: { type: 'string' }, city: { type: 'string' }, state: { type: 'string' }, zip: { type: 'string' },
      source_name: { type: 'string' }, source_url: { type: 'string' },
      note: { type: 'string', description: 'one sentence: why this organisation / what was wrong' },
    }, required: ['rid', 'employer_name', 'verdict', 'organisation', 'address', 'city', 'state', 'zip', 'source_name', 'source_url', 'note'] } },
  },
  required: ['rows'],
}
const CHECK = {
  type: 'object',
  properties: {
    checks: { type: 'array', items: { type: 'object', properties: {
      rid: { type: 'integer' },
      agree: { type: 'boolean' },
      fixed: { type: 'object', description: 'only when agree=false: your corrected row with the same fields as the researcher (verdict, organisation, address, city, state, zip, source_name, source_url, note)' },
      reason: { type: 'string' },
    }, required: ['rid', 'agree', 'reason'] } },
  },
  required: ['checks'],
}

const results = await pipeline(
  batches,
  (b) => agent(`${RULES}\n\nYOUR ROWS: rid ${b.from} to ${b.to} (inclusive). Return one output row per input rid, all of them.`,
    { label: `research:${b.from}-${b.to}`, phase: 'Research', schema: ROWS }),
  (res, b) => res && agent(`${RULES}\n\nYou are an INDEPENDENT CHECKER for rid ${b.from}-${b.to}. A researcher returned (JSON):\n${JSON.stringify(res.rows)}\n\nRe-verify yourself: EVERY row whose verdict is not 'correct' (open the source, confirm organisation identity against the donors' occupations/places, confirm the address), and at least 1 in 4 of the 'correct' rows (pick the riskiest: out-of-state, short/acronym names). Default to skepticism: a corrected address from the wrong namesake organisation, a residential street published as an office, or a source that does not state the address must be rejected. Return one check per re-verified rid; when you disagree, give your corrected row in 'fixed'.`,
    { label: `check:${b.from}-${b.to}`, phase: 'Check', schema: CHECK }).then(chk => ({ batch: b, rows: res.rows, checks: chk ? chk.checks : [] })),
)
return results.filter(Boolean)
