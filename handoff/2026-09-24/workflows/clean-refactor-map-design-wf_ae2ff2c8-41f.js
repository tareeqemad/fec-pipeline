export const meta = {
  name: 'clean-refactor-map-design',
  description: 'Read-only map of the cleaning code (steps, call graph, dead code, duplicates) and a behaviour-preserving restructuring design',
  phases: [
    { title: 'Map', detail: 'four read-only mappers over the cleaning code' },
    { title: 'Design', detail: 'one designer turns the maps into a target layout and ordered migration stages' },
  ],
}

const WT = 'C:\\Users\\PC\\AppData\\Local\\Temp\\claude\\C--Users-PC-Desktop-fec-pipeline\\ed385f69-648b-4ef4-b621-6658f7a540a2\\scratchpad\\refactor_wt'
const S = 'C:\\Users\\PC\\AppData\\Local\\Temp\\claude\\C--Users-PC-Desktop-fec-pipeline\\ed385f69-648b-4ef4-b621-6658f7a540a2\\scratchpad'

const COMMON = `
READ-ONLY. Work only in the git worktree ${WT} (branch refactor/clean-structure, commit 7ae5bb4f of the FEC pipeline). Do not edit any file; scratch notes only under ${S}\\refmap\\. Never run clean.py/resolve.py/geocode.py/loader.py/sync_rosters.py/build_employers.py (clean.py has no argparse; --help starts a full run).
GOAL of the whole effort (the owner's words): reorganise the cleaning code so that any Python programmer, or the owner, can open clean.py, follow the code step by step and know where to edit in future. NO .md documentation: the code itself must explain (one ordered step list, domain folders, step name = function name = audit step name, short module docstrings saying what it changes / which columns / which rules file). Behaviour must stay byte-identical (the cleaned CSV and the audit trail).
Entry: clean.py -> fec/cleaning/cli.py main -> fec/cleaning/pipeline/core.py clean_pipeline. Audit step names in the last run: data/audit_summary.json ('steps', 125 names).
`

const AREAS = [
  { key: 'orchestration', files: 'clean.py, fec/cleaning/cli.py, fec/cleaning/pipeline/core.py, fec/cleaning/pipeline/__init__.py, fec/cleaning/record_rules.py, fec/cleaning/audit*.py, fec/cleaning/audit_trail.py, fec/cleaning/quality.py, fec/cleaning/quality_scan.py, fec/io.py, fec/env.py, fec/log.py' },
  { key: 'names-identity', files: 'fec/cleaning/pipeline/names.py, fec/cleaning/name_rules.py, fec/cleaning/entity_classification.py (and any other name/entity modules), fec/donor_match/**, fec/cleaning/pipeline/donor_stage.py, fec/cleaning/foreign_addresses.py' },
  { key: 'occupations-employers', files: 'fec/cleaning/occupations/**, fec/cleaning/employer_synonyms/**, fec/cleaning/safety_nets/**, fec/cleaning/previous_employer.py, fec/cleaning/pipeline/reclassify*.py, fec/config/occupation_rules/**, fec/config/constants.py, fec/cleaning/donor_consistency/** (employer/occupation parts)' },
  { key: 'addresses', files: 'fec/cleaning/addresses.py, fec/cleaning/pipeline/address_stage.py, fec/cleaning/pipeline/address_fixes/**, fec/cleaning/address_review.py, fec/cleaning/pipeline/fec_recovery.py, fec/config/streets.py, fec/config/cities.py, fec/cleaning/donor_consistency/** (address parts), and the leftover directory fec/cleaning/addresses/ (only __pycache__?)' },
]

const MAP = {
  type: 'object',
  properties: {
    area: { type: 'string' },
    steps: { type: 'array', items: { type: 'object', properties: {
      step: { type: 'string', description: 'audit step name' }, function: { type: 'string' }, location: { type: 'string', description: 'file:line' },
      registered_in: { type: 'string', description: 'which list/call registers it (NAME_STEPS, _AUDITED_RULES, SAFETY_RULES, CONSISTENCY_FIXES, direct call in core.py ...)' },
      order: { type: 'string' }, changes_columns: { type: 'string' }, rules_source: { type: 'string', description: 'CSV/config the step reads, if any' },
    }, required: ['step', 'function', 'location', 'registered_in', 'order', 'changes_columns', 'rules_source'] } },
    helpers_used_across_modules: { type: 'array', items: { type: 'string' } },
    dead_code: { type: 'array', items: { type: 'string' }, description: 'functions/modules/constants never called (verify with grep across the whole repo incl. tests/tools/build_employers/resolve/geocoding/database)' },
    duplicates: { type: 'array', items: { type: 'string' }, description: 'functions doing the same job (e.g. the 4 employer-key functions, 3 synonym passes), with a safe consolidation and proof it is behaviour-identical or not' },
    hidden_rules: { type: 'array', items: { type: 'string' }, description: 'hand-maintained lists/dicts inside function bodies or scattered, that belong in config' },
    naming_mismatches: { type: 'array', items: { type: 'string' }, description: 'step name vs function name vs module name mismatches' },
    external_users: { type: 'array', items: { type: 'string' }, description: 'modules outside fec/cleaning (resolve, geocoding, database, build_employers, tools, tests) that import from these files — they constrain moves' },
    notes: { type: 'array', items: { type: 'string' } },
  },
  required: ['area', 'steps', 'helpers_used_across_modules', 'dead_code', 'duplicates', 'hidden_rules', 'naming_mismatches', 'external_users', 'notes'],
}

const DESIGN = {
  type: 'object',
  properties: {
    target_layout: { type: 'string', description: 'the proposed package/folder/file tree with one line per file saying what it holds' },
    step_list_design: { type: 'string', description: 'how the single ordered step list looks in code (example snippet), where it lives, how core.py/cli.py use it, how --list-steps and --help/--out-dir work' },
    stages: { type: 'array', items: { type: 'object', properties: {
      n: { type: 'integer' }, title: { type: 'string' }, what: { type: 'string', description: 'concrete moves/renames/merges/deletions, file by file' },
      risk: { type: 'string' }, proof: { type: 'string', description: 'how byte-identical output is proven for this stage' },
      touches_files_changed_on_main: { type: 'boolean', description: 'true if it touches fec/cleaning/addresses.py, fec/cleaning/pipeline/address_fixes/state_zip.py or fec/config/streets.py (being edited on main right now)' },
    }, required: ['n', 'title', 'what', 'risk', 'proof', 'touches_files_changed_on_main'] } },
    delete: { type: 'array', items: { type: 'string' } },
    keep_as_is: { type: 'array', items: { type: 'string' }, description: 'things that look messy but must not change (behavioural order dependencies etc.) and why' },
    external_import_updates: { type: 'array', items: { type: 'string' } },
  },
  required: ['target_layout', 'step_list_design', 'stages', 'delete', 'keep_as_is', 'external_import_updates'],
}

phase('Map')
const maps = await parallel(AREAS.map(a => () => agent(`${COMMON}\n\nMAP AREA '${a.key}': ${a.files}.\nList every audit step implemented in this area in execution order with its function, location, registration and columns; cross-check against data/audit_summary.json. Find dead code, duplicates, hidden rule lists, naming mismatches and external importers (grep the whole worktree). Be exact; line numbers from the worktree.`,
  { label: `map:${a.key}`, phase: 'Map', schema: MAP })))

phase('Design')
const design = await agent(`${COMMON}\n\nYou are the DESIGNER. Four mappers produced (JSON):\n${JSON.stringify(maps.filter(Boolean))}\n\nDesign the target structure and an ordered list of small, behaviour-preserving migration stages (each ending in a byte-identical clean replay and a green test suite, each a separate commit). Principles: (1) one ordered step list in one file that reads like the table of contents of the cleaning (stages -> steps), each entry pointing at the function (Ctrl+Click), with a one-line comment; core.py just runs it; (2) domain folders named after the stages; (3) step name == function name == audit step name (prefer renaming functions/modules, NOT audit step names, so the audit trail stays byte-identical); (4) hand-edited rules in clearly named config/CSV files, not inside function bodies; (5) delete dead code and consolidate proven duplicates only when identical; (6) keep external imports working (resolve, geocoding, database, build_employers, tools/audits, tests) — list every import to update; (7) clean.py gets argparse: --help, --list-steps (prints the ordered steps with file:function and last-run change counts from data/audit_summary.json), --out-dir (write outputs elsewhere). (8) docs/CODE_MAP.md is deleted (the owner asked) — list references to it to remove (README etc.). Put stages that touch fec/cleaning/addresses.py, fec/cleaning/pipeline/address_fixes/state_zip.py or fec/config/streets.py LAST (those files are being edited on main right now). Do not over-engineer: no new frameworks, no class hierarchies; plain functions and tuples.`,
  { label: 'design', phase: 'Design', schema: DESIGN })
return { maps: maps.filter(Boolean), design }
