-- ============================================================
--  FEC Database - Normalized Schema v1.2
--  Key Accomplices / DonorMap
-- ============================================================
--
--  This file lives as an EXACT copy in two repos:
--    fec-pipeline/fec/database/schema.sql        (source of truth, loader.py runs it)
--    accomplices-demo/test/db/schema.sql         (dashboard DB-contract test fixture)
--  Edit the pipeline one, then copy it over the dashboard one.
--
--  Design principles:
--    1. Third normal form - no redundant data
--    2. Single source of truth per fact
--    3. Simple views (no LATERAL, no deep joins) - compose from
--       small sub-views that each do one thing
--    4. Strong constraints: FKs, CHECKs, and UNIQUE - including
--       `UNIQUE NULLS NOT DISTINCT` (PG 15+) for dedup keys whose parts can be
--       NULL (donor_employments). Plain UNIQUE keeps multiple NULLs distinct,
--       which is exactly what `committee_number` wants for non-FEC orgs. The
--       pre-15 idiom (several partial UNIQUE indexes to cover each NULL
--       combination) is not needed on this PG 18 box.
--       EXCEPTION (deliberate): the reference tables in section 10 (us_states,
--       zcta_state_rel, zip_centroids) are intentionally standalone - the
--       address fields state_code / zip_code are NOT FK-bound to them, because
--       the lookup data is not exhaustive: us_states omits US territories
--       (live data has PR + VI addresses), and zip_centroids is a partial
--       geocoding fallback (~394 zips in live addresses are not in it). Binding
--       FKs would reject legitimate rows, so these stay free-text by design.
--    5. `employers.address_id` is the default company location.
--       `donor_employments.address_id` stores a same-state company office when
--       available, otherwise the default company location
--       for that donor; both point to the shared addresses dimension.
--
--  History (structural changes folded into the current v1.2 schema):
--      - DROPPED `contributions_cleaned` (redundant with the normalized
--        tables); replaced with the `v_contributions_cleaned` view
--      - DROPPED `donors.current_*` denormalized pointers; replaced with
--        `v_donor_current_address` / `v_donor_current_employment`
--      - MOVED per-employment status (employer_status / previous_employer)
--        onto `donor_employments`
--      - SIMPLIFIED all views - no more LATERAL joins
--      - MERGED the old 1:1 employer-location table into `employers`; additional
--        offices now use donor_employments.address_id
--      - REPLACED the single `leadership.committee_id` with the `leaders`
--        table + M:N `leader_committees` junction (FKs enforced both sides)
--      - ADDED `UNIQUE NULLS NOT DISTINCT (donor_id, employer_id, occupation)`
--        on `donor_employments`
--      - ADDED `UNIQUE` on us_states.code; range CHECKs on addresses /
--        zip_centroids lat-lng; `raised/spent >= 0` CHECKs on committees
--    Per-view tweaks carry their own inline notes (e.g. v_contributions_cleaned).
--
-- ============================================================


-- Requires pg_trgm, cube, and earthdistance; loader validates them before reset.

-- ----------------------------------------------------------
--  1. Lookup tables
-- ----------------------------------------------------------

CREATE TABLE occupation_categories (
    occupation_category_id  SERIAL  PRIMARY KEY,
    name    TEXT    NOT NULL UNIQUE
);


-- ----------------------------------------------------------
--  2. Dimension tables
-- ----------------------------------------------------------

-- FEC-registered committees + related orgs (AIEF, ZOA, ...)
CREATE TABLE committees (
    committee_id        SERIAL          PRIMARY KEY,
    committee_number    TEXT            UNIQUE,            -- NULL for non-FEC orgs
    committee_name      TEXT            NOT NULL,
    committee_short     TEXT,                              -- was VARCHAR(100); TEXT for schema-wide consistency (identical perf in PG)
    raised              NUMERIC(15, 2)  CHECK (raised >= 0),
    spent               NUMERIC(15, 2)  CHECK (spent  >= 0),
    irs_990_link        TEXT,
    -- Single source of truth for the committee's display logo. Pages that
    -- show a committee badge (key_accomplices cards, leadership cards,
    -- donor-profile committee breakdown) read it from here via JOIN
    -- instead of hard-coding paths client-side.
    logo_path           TEXT
);


-- Unique donors (individuals + contributing organizations).
-- One row per `donor_key` (identity hash from donor_match).
-- NO denormalized "latest" pointers - those are derived via views.
CREATE TABLE donors (
    donor_id        SERIAL  PRIMARY KEY,
    donor_key       TEXT    NOT NULL UNIQUE,
    entity_type     TEXT    NOT NULL CHECK (entity_type IN ('INDIVIDUAL', 'ORGANIZATION', 'COMMITTEE/PAC')),
    first_name      TEXT,                       -- NULL for committees
    last_name       TEXT                        -- committee full name for COMMITTEE/PAC
);


-- --- Shared address dimension - one source of truth for EVERY address ---
-- A donor's residence (via donor_addresses) and an employer location (via
-- employers.address_id) both point here, so a physical address is stored and
-- geocoded exactly once - and a self-employed donor's reported and work
-- address point to the same row. Privacy (the 500-800 m map fuzz) is
-- applied at the API layer on the donor-facing path; it is never stored here.
CREATE TABLE addresses (
    address_id  SERIAL              PRIMARY KEY,
    street_1    TEXT,
    street_2    TEXT,
    city        TEXT,
    state_code  TEXT,
    zip_code    TEXT,
    latitude    DOUBLE PRECISION    CHECK (latitude  BETWEEN  -90 AND  90),
    longitude   DOUBLE PRECISION    CHECK (longitude BETWEEN -180 AND 180)
    -- No DB-level UNIQUE - the loader dedups on the full address tuple
    -- (cleaning lives in the pipeline, not a strict constraint).
);


-- Unique companies. address_id is the default company location; a donor's
-- selected company location lives on donor_employments.address_id.
CREATE TABLE employers (
    employer_id SERIAL  PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    address_id  INT     REFERENCES addresses(address_id)
);


-- ----------------------------------------------------------
--  3. History / bridge tables
-- ----------------------------------------------------------

-- Links a donor to an address (in the shared `addresses` table). The "when"
-- (first/last donation at this address) is NOT stored - it's derived on read
-- from contributions (MIN/MAX receipt_date GROUP BY donor_address_id), so there's
-- one source of truth for dates and no cached column to drift.
CREATE TABLE donor_addresses (
    donor_address_id    SERIAL              PRIMARY KEY,
    donor_id            INT                 NOT NULL REFERENCES donors(donor_id),
    address_id          INT                 NOT NULL REFERENCES addresses(address_id)
    -- No DB-level UNIQUE - the loader dedups (donor, address) upstream
    -- (groupby on the full address + the leaders loader's check-then-insert),
    -- so the DB just stores already-deduped link rows.
);


-- Every distinct (donor, employer, occupation) tuple.
-- `employer_id` is NULL when the donor is RETIRED / NOT EMPLOYED /
-- SELF-EMPLOYED / HOMEMAKER / STUDENT / (committee).
-- For RETIRED donors, `previous_employer_id` points at their prior
-- employer (filled by resolve.py's cache + donor consistency).
CREATE TABLE donor_employments (
    donor_employment_id     SERIAL              PRIMARY KEY,
    donor_id                INT                 NOT NULL REFERENCES donors(donor_id),
    employer_id             INT                 REFERENCES employers(employer_id),
    occupation              TEXT,
    occupation_category_id  INT                 REFERENCES occupation_categories(occupation_category_id),
    -- Per-employment attributes (moved from contributions_cleaned)
    employer_status         TEXT                CHECK (employer_status IN (
                                                    'active', 'retired', 'self_employed',
                                                    'not_employed', 'committee',
                                                    'organization', 'missing')),
    previous_employer_id    INT                 REFERENCES employers(employer_id),
    -- Self-employed: the donor's reported address. Active/retired: a same-state
    -- company office, falling back to the primary company location. Not-employed:
    -- NULL. A company-office selection is an inference, not proof of workplace.
    address_id              INT                 REFERENCES addresses(address_id),
    -- "When" (first/last seen, career timeline) is NOT stored - derive it from
    -- contributions (MIN/MAX receipt_date GROUP BY donor_employment_id). One source
    -- of truth for dates; no cached column to drift.
    --
    -- DB-level guard: one row per distinct (donor, employer, occupation).
    -- NULLS NOT DISTINCT (Postgres 15+) so RETIRED / SELF-EMPLOYED / NOT-EMPLOYED
    -- rows - where employer_id and/or occupation are NULL - also collapse, instead
    -- of slipping past a plain UNIQUE that treats every NULL as distinct. The
    -- loader's groupby on (donor_key, employer, occupation) plus a Python seen-set
    -- already produce deduped rows; this promotes that convention to an enforced
    -- invariant (verified 0 violations on the live DB). Career progression
    -- (ASSOCIATE -> PARTNER) still yields separate rows because the occupation differs.
    UNIQUE NULLS NOT DISTINCT (donor_id, employer_id, occupation)
);


-- ----------------------------------------------------------
--  4. Fact table - contributions
-- ----------------------------------------------------------

-- One row per FEC filing. Foreign keys only - no denormalized copies.
CREATE TABLE contributions (
    sub_id              BIGINT          PRIMARY KEY,    -- FEC's numeric filing id (19 digits). Fits BIGINT:
                                                        -- observed max ~4.12e18, ~45% of the 9.22e18 ceiling.
                                                        -- If FEC ever issues ids >= 9.22e18, switch to NUMERIC(20).
    transaction_id      TEXT            NOT NULL,
    donor_id            INT             NOT NULL REFERENCES donors(donor_id),
    committee_id        INT             NOT NULL REFERENCES committees(committee_id),
    donor_address_id    INT             REFERENCES donor_addresses(donor_address_id),
    donor_employment_id INT             REFERENCES donor_employments(donor_employment_id),
    amount              NUMERIC(15, 2)  NOT NULL,       -- matches committees.raised/spent precision
    receipt_date        DATE            NOT NULL,
    election_cycle      INTEGER         NOT NULL
);


-- Employer locations share the addresses dimension. The CSV may contain
-- several known offices; the loader stores the selected one per employment.


-- NOTE: `contributions_cleaned` table dropped. The same
-- flat layout is now available via the VIEW `v_contributions_cleaned`
-- defined below, which reads from the normalized tables. No storage
-- duplication, no sync burden.


-- ----------------------------------------------------------
--  5. Indexes
-- ----------------------------------------------------------

-- donors
CREATE INDEX idx_donors_entity      ON donors (entity_type);
CREATE INDEX idx_donors_last        ON donors (last_name);
CREATE INDEX idx_donors_first_last  ON donors (last_name, first_name);
CREATE INDEX idx_donors_name_trgm   ON donors USING gin (last_name gin_trgm_ops);

-- addresses (shared dimension - location filters + geo live here now)
CREATE INDEX idx_addr_state         ON addresses (state_code);
CREATE INDEX idx_addr_zip           ON addresses (zip_code);
CREATE INDEX idx_addr_city          ON addresses (city);
CREATE INDEX idx_addr_geo           ON addresses (latitude, longitude)
    WHERE latitude IS NOT NULL;

-- donor_addresses (donor <-> address link)
CREATE INDEX idx_da_donor           ON donor_addresses (donor_id);
CREATE INDEX idx_da_address         ON donor_addresses (address_id);

-- donor_employments
CREATE INDEX idx_de_donor           ON donor_employments (donor_id);
CREATE INDEX idx_de_employer        ON donor_employments (employer_id);
CREATE INDEX idx_de_occ_cat         ON donor_employments (occupation_category_id);
CREATE INDEX idx_de_prev_employer   ON donor_employments (previous_employer_id);

-- contributions
CREATE INDEX idx_c_donor            ON contributions (donor_id);
CREATE INDEX idx_c_committee        ON contributions (committee_id);
CREATE INDEX idx_c_date             ON contributions (receipt_date);
CREATE INDEX idx_c_cycle            ON contributions (election_cycle);
CREATE INDEX idx_c_donor_address    ON contributions (donor_address_id);
CREATE INDEX idx_c_donor_employment ON contributions (donor_employment_id);
CREATE INDEX idx_c_donor_date       ON contributions (donor_id, receipt_date DESC);

-- employers (entity; default location via address_id)
CREATE INDEX idx_emp_name           ON employers (name);
CREATE INDEX idx_emp_name_trgm      ON employers USING gin (name gin_trgm_ops);
CREATE INDEX idx_emp_address        ON employers (address_id);


-- ----------------------------------------------------------
--  6. "Latest per donor" views - the foundation of all donor-level
--     reads. DISTINCT ON is Postgres' way to grab "newest per X".
-- ----------------------------------------------------------

-- One row per donor - their newest address.
CREATE OR REPLACE VIEW v_donor_current_address AS
SELECT DISTINCT ON (c.donor_id)
    c.donor_id,
    addr.address_id,
    addr.street_1,
    addr.street_2,
    addr.city,
    addr.state_code,
    addr.zip_code,
    addr.latitude,
    addr.longitude
FROM contributions c
JOIN donor_addresses da   ON da.donor_address_id = c.donor_address_id
JOIN addresses       addr ON addr.address_id     = da.address_id
ORDER BY
    c.donor_id,
    c.receipt_date DESC,
    c.sub_id       DESC;          -- tiebreaker (same day)


-- One row per donor - their newest employer + occupation.
CREATE OR REPLACE VIEW v_donor_current_employment AS
SELECT DISTINCT ON (c.donor_id)
    c.donor_id,
    e.donor_employment_id,
    e.employer_id,
    e.occupation,                 -- raw job title
    e.occupation_category_id,     -- normalized category (FK)
    e.employer_status,            -- active / retired / not_employed / ...
    e.previous_employer_id,       -- last real job before retirement
    e.address_id                  -- selected workplace
FROM contributions c
JOIN donor_employments e ON e.donor_employment_id = c.donor_employment_id
ORDER BY
    c.donor_id,
    c.receipt_date DESC,
    c.sub_id       DESC;


-- ----------------------------------------------------------
--  7. Aggregate views - one number per group
-- ----------------------------------------------------------

-- One row per donor - their lifetime totals.
-- Used by v_donor_profile.
CREATE OR REPLACE VIEW v_donor_stats AS
SELECT
    c.donor_id,
    COUNT(c.sub_id)                  AS donation_count,
    SUM(c.amount)                    AS total_amount,
    AVG(c.amount)                    AS avg_amount,
    MIN(c.receipt_date)              AS first_donation,
    MAX(c.receipt_date)              AS last_donation,
    COUNT(DISTINCT c.election_cycle) AS election_cycles
FROM contributions c
GROUP BY c.donor_id;


-- ----------------------------------------------------------
--  8. Top-level composed views - built from the sub-views above
-- ----------------------------------------------------------

-- Full donor profile - identity + stats + current address + current job
-- + previous employer (for retired donors). Powers the dashboard.
CREATE OR REPLACE VIEW v_donor_profile AS
SELECT
    -- Identity
    d.donor_id,
    d.donor_key,
    d.entity_type,
    d.first_name,
    d.last_name,

    -- Lifetime stats (from v_donor_stats)
    s.donation_count,
    s.total_amount,
    s.avg_amount,
    s.first_donation,
    s.last_donation,
    s.election_cycles,

    -- Current address (from v_donor_current_address)
    a.street_1      AS current_street,
    a.city          AS current_city,
    a.state_code    AS current_state,
    a.zip_code      AS current_zip,
    a.latitude      AS current_lat,
    a.longitude     AS current_lng,

    -- Current employer + occupation (from v_donor_current_employment)
    emp.name        AS current_employer,
    e.occupation    AS current_occupation,
    oc.name         AS current_occ_category,
    e.employer_status,

    -- Previous employer - set when donor is now RETIRED so we still
    -- know where they USED TO work.
    prev.name       AS previous_employer
FROM donors d
LEFT JOIN v_donor_stats              s    ON s.donor_id   = d.donor_id
LEFT JOIN v_donor_current_address    a    ON a.donor_id   = d.donor_id
LEFT JOIN v_donor_current_employment e    ON e.donor_id   = d.donor_id
LEFT JOIN employers                  emp  ON emp.employer_id           = e.employer_id
LEFT JOIN occupation_categories      oc   ON oc.occupation_category_id = e.occupation_category_id
LEFT JOIN employers                  prev ON prev.employer_id          = e.previous_employer_id;


-- Flat per-contribution view - one row per FEC filing with the donor,
-- committee, address, employer and occupation all denormalized onto it.
-- Consumed by the healthcheck's flat-view checks (fec/database/query_checks.py);
-- the web app queries the normalized tables directly. Zero storage - pure
-- read layer over the normalized tables.
CREATE OR REPLACE VIEW v_contributions_cleaned AS
SELECT
    c.sub_id,
    c.transaction_id,
    c.election_cycle                        AS two_year_transaction_period,
    d.donor_key,
    EXTRACT(YEAR FROM c.receipt_date)::INT  AS contributor_year,
    cm.committee_short                      AS recipient_committee,
    d.entity_type,
    d.first_name                            AS contributor_first_name,
    d.last_name                             AS contributor_last_name,
    CASE
        WHEN d.entity_type = 'INDIVIDUAL'
         AND d.first_name IS NOT NULL
         AND d.last_name  IS NOT NULL
        THEN d.last_name || ', ' || d.first_name
        ELSE d.last_name
    END                                     AS contributor_name,
    ca.street_1                             AS contributor_street_1,
    ca.street_2                             AS contributor_street_2,
    ca.city                                 AS contributor_city,
    ca.state_code                           AS contributor_state,
    ca.zip_code                             AS contributor_zip,
    ca.latitude,
    ca.longitude,
    -- real company name only - NULL for retired/not-employed (status lives in employer_status)
    emp.name                                AS contributor_employer,
    de.occupation                           AS contributor_occupation,
    oc.name                                 AS occupation_category,
    de.employer_status,
    prev_emp.name                           AS previous_employer,
    c.amount                                AS contribution_receipt_amount,
    c.receipt_date                          AS contribution_receipt_date,
    ea.street_1                             AS employer_address,
    ea.city                                 AS employer_city,
    ea.state_code                           AS employer_state,
    ea.zip_code                             AS employer_zip,
    ea.latitude                             AS employer_latitude,
    ea.longitude                            AS employer_longitude
FROM contributions c
JOIN donors                     d        ON d.donor_id                = c.donor_id
JOIN committees                 cm       ON cm.committee_id           = c.committee_id
LEFT JOIN donor_addresses       a        ON a.donor_address_id        = c.donor_address_id
LEFT JOIN addresses             ca       ON ca.address_id             = a.address_id
LEFT JOIN donor_employments     de       ON de.donor_employment_id    = c.donor_employment_id
LEFT JOIN employers             emp      ON emp.employer_id           = de.employer_id
LEFT JOIN employers             prev_emp ON prev_emp.employer_id      = de.previous_employer_id
LEFT JOIN addresses             ea       ON ea.address_id = COALESCE(
                                                    de.address_id,
                                                    emp.address_id,
                                                    CASE
                                                        WHEN de.employer_status = 'retired'
                                                        THEN prev_emp.address_id
                                                    END
                                                )
LEFT JOIN occupation_categories oc       ON oc.occupation_category_id = de.occupation_category_id;


-- ----------------------------------------------------------
--  9. Materialized views - pre-computed for dashboard speed
-- ----------------------------------------------------------

-- Pre-computed snapshot of v_donor_profile - the dashboard reads from here.
-- Why: v_donor_profile is 6 LEFT JOINs wide. On millions of rows that's slow
-- to run each request, so we cache the result and refresh after each loader run.
CREATE MATERIALIZED VIEW mv_donor_profile AS
SELECT * FROM v_donor_profile;

-- Indexes - each one speeds up a specific dashboard query.
CREATE UNIQUE INDEX idx_mvdp_id     ON mv_donor_profile (donor_id);             -- /donor/<id>
CREATE INDEX idx_mvdp_key           ON mv_donor_profile (donor_key);            -- /donor/<key>
CREATE INDEX idx_mvdp_entity        ON mv_donor_profile (entity_type);          -- "individuals only"
CREATE INDEX idx_mvdp_total         ON mv_donor_profile (total_amount DESC NULLS LAST);  -- "Top 100 donors"
CREATE INDEX idx_mvdp_state         ON mv_donor_profile (current_state);        -- state filter
CREATE INDEX idx_mvdp_state_total   ON mv_donor_profile (current_state, total_amount DESC NULLS LAST);  -- "Top donors in NY"
CREATE INDEX idx_mvdp_name_trgm     ON mv_donor_profile USING gin (last_name gin_trgm_ops);  -- fuzzy name search
CREATE INDEX idx_mvdp_employer      ON mv_donor_profile (current_employer);     -- "everyone at Google"
CREATE INDEX idx_mvdp_prev_employer ON mv_donor_profile (previous_employer);    -- "everyone who USED to work at Google" (retirees' last job)
CREATE INDEX idx_mvdp_occ           ON mv_donor_profile (current_occ_category); -- occupation filter
CREATE INDEX idx_mvdp_geo           ON mv_donor_profile (current_lat, current_lng)
    WHERE current_lat IS NOT NULL;                                              -- map bounding-box


-- The refresh runs at the end of every loader run from Python
-- (fec/database/loader/__init__.py refresh_materialized_views):
--   REFRESH MATERIALIZED VIEW CONCURRENTLY mv_donor_profile
-- CONCURRENTLY = no downtime, and it requires the UNIQUE index above.
-- (An old SQL wrapper function of the same name had zero callers - removed.)


-- ----------------------------------------------------------
-- 10. Reference tables (loaded from data/database/*.csv)
-- ----------------------------------------------------------

CREATE TABLE us_states (
    state_fips  TEXT    NOT NULL PRIMARY KEY,
    code        TEXT    NOT NULL UNIQUE,        -- USPS 2-letter code; one per state (verified 0 dups)
    name        TEXT    NOT NULL
);

CREATE TABLE zcta_state_rel (
    zcta5       TEXT    NOT NULL PRIMARY KEY,
    state_fips  TEXT    NOT NULL REFERENCES us_states(state_fips)
);

CREATE TABLE zip_centroids (
    zip         TEXT                NOT NULL PRIMARY KEY,
    lat         DOUBLE PRECISION    NOT NULL CHECK (lat BETWEEN  -90 AND  90),
    lng         DOUBLE PRECISION    NOT NULL CHECK (lng BETWEEN -180 AND 180),
    source      TEXT                NOT NULL DEFAULT 'census_gazetteer_2023',
    fetched_at  TIMESTAMPTZ         DEFAULT NOW()       -- was TIMESTAMP; TZ-aware is the safer default
);
CREATE INDEX idx_zip_centroids_geo ON zip_centroids (lat, lng);


-- Key Accomplices - dashboard feature content.
--
-- Identity is normalized the same way as the leadership table:
--   - Personal identity (name) lives in `donors` - joined via donor_id.
--   - Physical addresses live in `donor_addresses` - joined via donor_id.
--     The /api/maps?accomplice=<donor_key> endpoint reads
--     donor_addresses.latitude/longitude and applies a random 500-800 m
--     offset at click time, so the public map link never exposes the
--     exact street point.
--   - Employment lives in `donor_employments` (when available in FEC).
--   - External URLs / API references go through donors.donor_key
--     (stable across DB reloads) - no separate slug needed.
--
-- `sign` is the deck-of-cards identifier used by the landing page
-- visualization (e.g. "Ace Spades", "King Hearts", "Joker"). Nullable.
-- `body_text` is plain text (was body_md - markdown rendering removed).
-- `display_order` preserves the curator-defined ordering from the CSV.
CREATE TABLE key_accomplices (
    accomplice_id   SERIAL          PRIMARY KEY,
    -- One key_accomplices row per donor (UNIQUE). The 5 people who appear
    -- in both leadership and key_accomplices (Howard Kohr, Mark Mellman,
    -- Elliot Brandt, Ed Levy Jr, Stacy Schusterman) share the same
    -- donor_id across both tables - one identity, two roles.
    donor_id        INTEGER         NOT NULL UNIQUE REFERENCES donors(donor_id),
    sign            TEXT,                                       -- was: sing
    subtitle        TEXT,
    body_text       TEXT,                                       -- was: body_md
    -- FK to the org / PAC this accomplice is most closely associated with.
    -- The card's logo is rendered from committees.logo_path via JOIN.
    -- NULL for accomplices not affiliated with any tracked committee.
    committee_id    INTEGER         REFERENCES committees(committee_id),
    display_order   INTEGER         NOT NULL DEFAULT 0          -- was: sort
);


-- Person portraits - one image per donor (1:1). The image is a property of the
-- PERSON, not their role, so accomplices and leaders share a single source of
-- truth (the 5 overlap people get one row, surfaced on both pages via JOIN).
CREATE TABLE donor_images (
    donor_id   INTEGER PRIMARY KEY REFERENCES donors(donor_id) ON DELETE CASCADE,
    image_path TEXT NOT NULL
);
CREATE INDEX idx_key_accomplices_sign  ON key_accomplices (sign) WHERE sign IS NOT NULL;
CREATE INDEX idx_key_accomplices_order ON key_accomplices (display_order);


-- Helper view: one row per donor - newest address from donor_addresses.
-- Different from v_donor_current_address: doesn't require a contribution,
-- so it covers leaders who never donated.
CREATE OR REPLACE VIEW v_donor_newest_address AS
SELECT DISTINCT ON (da.donor_id)
    da.donor_id,
    addr.street_1,
    addr.street_2,
    addr.city,
    addr.state_code,
    addr.zip_code,
    addr.latitude,
    addr.longitude
FROM donor_addresses da
JOIN addresses addr ON addr.address_id = da.address_id
-- Newest = the address used in the donor's latest contribution, derived from
-- contributions (no stored date). NULL for leaders who never donated -> the
-- id tiebreaker picks their single address.
LEFT JOIN (
    SELECT donor_address_id, MAX(receipt_date) AS last_seen
    FROM contributions
    GROUP BY donor_address_id
) cd ON cd.donor_address_id = da.donor_address_id
ORDER BY
    da.donor_id,
    cd.last_seen DESC NULLS LAST,
    da.donor_address_id DESC;       -- tiebreaker (covers non-donors)


-- Helper view: one row per donor - newest employment from donor_employments.
-- Like v_donor_newest_address, this reads the table DIRECTLY (no contribution
-- needed), so it covers leaders who never donated to FEC but whose employer
-- came from leaders.csv. v_leaders reads employment from here.
CREATE OR REPLACE VIEW v_donor_newest_employment AS
SELECT DISTINCT ON (e.donor_id)
    e.donor_id,
    emp.name        AS current_employer,     -- name resolved here, once
    e.occupation    AS current_occupation,
    e.employer_status,
    prev.name       AS previous_employer,     -- for "Retired - Previously at X"
    e.employer_id,
    e.occupation_category_id,
    e.previous_employer_id
FROM donor_employments e
LEFT JOIN employers emp  ON emp.employer_id  = e.employer_id
LEFT JOIN employers prev ON prev.employer_id = e.previous_employer_id
-- Newest = the employment in the donor's latest contribution, derived from
-- contributions (no stored date). NULL for leaders who never donated -> the
-- id tiebreaker picks their single employment.
LEFT JOIN (
    SELECT donor_employment_id, MAX(receipt_date) AS last_seen
    FROM contributions
    GROUP BY donor_employment_id
) ce ON ce.donor_employment_id = e.donor_employment_id
ORDER BY
    e.donor_id,
    ce.last_seen DESC NULLS LAST,
    e.donor_employment_id DESC;     -- tiebreaker (covers non-donors)


-- One row per "key accomplice" - used by the dashboard cards.
-- Brings together: donor identity + newest address + card content + linked committee.
-- No ORDER BY here - the API orders by display_order (the curator's card order).
--
-- PERF NOTE (measured 2026-06-06, ~200k contributions): v_donor_newest_address
-- has no per-donor filter, so it materializes "newest address for EVERY donor"
-- and only then joins down to the ~80 accomplices - a full Seq Scan of
-- contributions per call (~60ms), and it does NOT push a `WHERE donor_key = ...`
-- filter down. Fine at this size; if contributions grows large, promote
-- v_key_accomplices and v_leaders to MATERIALIZED VIEWs refreshed by the
-- loader's refresh step (fec/database/loader/__init__.py
-- refresh_materialized_views) alongside mv_donor_profile.
CREATE OR REPLACE VIEW v_key_accomplices AS
SELECT
    -- Identity
    k.accomplice_id,
    d.donor_id,
    d.donor_key,
    d.first_name,
    d.last_name,
    -- "First Last" (CONCAT_WS skips a missing piece automatically)
    CONCAT_WS(' ', d.first_name, d.last_name) AS full_name,

    -- Newest address (from v_donor_newest_address - works for non-donors too)
    addr.street_1,
    addr.street_2,
    addr.city,
    addr.state_code,
    addr.zip_code,
    addr.latitude,
    addr.longitude,

    -- Card content fields
    k.sign,
    k.subtitle,
    k.body_text,
    di.image_path,
    k.display_order,

    -- The committee this person is linked to (logo, name, etc.)
    k.committee_id,
    cm.committee_name,
    cm.committee_short,
    cm.logo_path AS committee_logo
FROM key_accomplices k
JOIN donors d                         ON d.donor_id = k.donor_id
LEFT JOIN committees cm               ON cm.committee_id = k.committee_id
LEFT JOIN v_donor_newest_address addr ON addr.donor_id = d.donor_id
LEFT JOIN donor_images di             ON di.donor_id = d.donor_id;


-- Leadership - AIPAC / DMFI / etc. leadership roster.
--
-- Identity is normalized - one source of truth for each person:
--   - Personal identity (name) lives in `donors` - joined here via donor_id.
--     For the 7 leaders who never donated to FEC, loader.py auto-creates
--     a donor row with a generated donor_key.
--   - Physical addresses live in `donor_addresses` - joined via donor_id.
--     The /api/maps?leader=X endpoint reads donor_addresses.latitude/longitude
--     and applies a random 500-800 m offset at click time, so the public
--     map link never exposes the exact street point.
--   - Employment (e.g. "AIPAC") lives in `donor_employments` - joined via donor_id.
--
-- A leader's committee memberships live in the `leader_committees` junction
-- below - a real M:N with enforced FKs (a committee_id can no longer point at a
-- committee that doesn't exist), consistent with the donor_addresses /
-- donor_employments bridge tables.
CREATE TABLE leaders (
    leader_id                   SERIAL              PRIMARY KEY,
    -- One leadership row per donor (UNIQUE). The 5 people who appear in both
    -- leadership and key_accomplices (Howard Kohr, Mark Mellman, Elliot
    -- Brandt, Ed Levy Jr, Stacy Schusterman) share the same donor_id across
    -- both tables - one identity, two roles.
    --
    -- External URLs / API references go through donors.donor_key (stable
    -- across DB reloads) - no separate slug needed. /leader/<donor_key>.
    donor_id                    INTEGER             NOT NULL UNIQUE REFERENCES donors(donor_id)
);


-- Junction: which committees each leader sits on (M:N). Composite PK gives
-- free uniqueness; both FKs are enforced, and ON DELETE CASCADE clears a
-- leader's links when that leader row is removed.
CREATE TABLE leader_committees (
    leader_id       INTEGER     NOT NULL REFERENCES leaders(leader_id) ON DELETE CASCADE,
    committee_id    INTEGER     NOT NULL REFERENCES committees(committee_id),
    PRIMARY KEY (leader_id, committee_id)
);
-- Reverse lookup: "which leaders sit on committee X".
CREATE INDEX idx_lc_committee ON leader_committees (committee_id);


-- One row per "leader" - used by AIPAC / DMFI / etc. dashboard pages.
-- Flat read: identity from donors + address + employment from their helper
-- views (which already pick the newest row and resolve the employer name).
-- Committee membership is M:N (a leader can sit on several committees) and is
-- NOT carried here - the per-committee page filters the junction directly:
--   JOIN leader_committees lc ON lc.leader_id = v.leader_id
--   WHERE lc.committee_id = :committee_id
-- No ORDER BY here - sorting is the query's job (the API orders by name);
-- a view's ORDER BY isn't guaranteed once it's wrapped or joined anyway.
CREATE OR REPLACE VIEW v_leaders AS
SELECT
    l.leader_id,
    d.donor_id,
    d.donor_key,
    d.first_name,
    d.last_name,
    CONCAT_WS(' ', d.first_name, d.last_name) AS full_name,

    -- Newest address (works for non-donor leaders too)
    a.street_1,
    a.street_2,
    a.city,
    a.state_code,
    a.zip_code,
    a.latitude,
    a.longitude,

    -- Current job. employer is NULL for RETIRED / NOT EMPLOYED - the UI then
    -- falls back to occupation / employer_status. previous_employer powers
    -- "Retired - Previously at AIPAC".
    e.current_employer,
    e.current_occupation,
    e.employer_status,
    e.previous_employer,

    -- Personal portrait (NULL/'' -> silhouette in the UI)
    di.image_path
FROM leaders l
JOIN donors d                          ON d.donor_id = l.donor_id
LEFT JOIN v_donor_newest_address a     ON a.donor_id = d.donor_id
LEFT JOIN v_donor_newest_employment e  ON e.donor_id = d.donor_id
LEFT JOIN donor_images di              ON di.donor_id = d.donor_id;


CREATE OR REPLACE VIEW v_curated_people AS
SELECT
    COALESCE(l.donor_id, k.donor_id) AS donor_id,
    COALESCE(l.donor_key, k.donor_key) AS donor_key,
    COALESCE(l.full_name, k.full_name) AS full_name,
    COALESCE(l.city, k.city) AS city,
    COALESCE(l.state_code, k.state_code) AS state_code,
    COALESCE(l.zip_code, k.zip_code) AS zip_code,
    p.current_employer,
    p.current_occupation,
    p.total_amount,
    array_remove(
        ARRAY[
            CASE
                WHEN l.leader_id IS NOT NULL THEN 'leader'
            END,
            CASE
                WHEN k.accomplice_id IS NOT NULL THEN 'key_accomplice'
            END
        ],
        NULL
    ) AS roles,
    k.subtitle,
    k.body_text,
    k.committee_name,
    k.committee_short,
    k.display_order
FROM v_leaders l
FULL OUTER JOIN v_key_accomplices k
    ON k.donor_id = l.donor_id
JOIN mv_donor_profile p
    ON p.donor_id = COALESCE(l.donor_id, k.donor_id);


-- --- Unified company view - a firm's employees AND its own donation, one row ---
-- A real company can show up two ways: as an EMPLOYER (people who work there and
-- donated) and - if the firm itself gave - as an ORGANIZATION donor. The cleaning
-- step align_org_donor_company_names() rewrites the org-donor's name to the
-- employer firm's canonical spelling (reusing canonical_key), so the two join by
-- a PLAIN name match here - no second normalization, no extra column. One row per
-- employer firm: its employee-donors, and whether/how much the firm itself gave.
-- (A firm that ONLY donated and employs no one stays a plain ORGANIZATION donor.)
CREATE OR REPLACE VIEW v_company AS
WITH emp_agg AS (
    SELECT de.employer_id,
           COUNT(DISTINCT de.donor_id)   AS employee_donor_count,
           COALESCE(SUM(c.amount), 0)    AS employee_total
    FROM donor_employments de
    LEFT JOIN contributions c ON c.donor_employment_id = de.donor_employment_id
    GROUP BY de.employer_id
),
org_self AS (   -- a firm that itself donated, keyed by the now-aligned name
    SELECT d.last_name     AS name,
           SUM(c.amount)   AS firm_donation_total,
           COUNT(*)        AS firm_donation_count
    FROM donors d
    JOIN contributions c ON c.donor_id = d.donor_id
    WHERE d.entity_type = 'ORGANIZATION'
    GROUP BY d.last_name
),
org_addr AS (   -- the firm's own filing address (org-donor addresses are 100%
                -- complete) - a fallback when the employer lookup was empty.
                -- Kept SEPARATE from org_self so a firm with >1 address never
                -- multiplies its donation sum.
    SELECT d.last_name AS name, MAX(da.address_id) AS org_address_id
    FROM donors d
    JOIN donor_addresses da ON da.donor_id = d.donor_id
    WHERE d.entity_type = 'ORGANIZATION'
    GROUP BY d.last_name
)
SELECT
    e.employer_id,
    e.name                                AS company_name,
    COALESCE(ea.employee_donor_count, 0)  AS employee_donor_count,
    COALESCE(ea.employee_total, 0)        AS employee_total,
    (os.name IS NOT NULL)                 AS firm_also_donated,
    COALESCE(os.firm_donation_total, 0)   AS firm_donation_total,
    -- Default company location, then the firm's own filing address.
    a.street_1, a.city, a.state_code, a.zip_code, a.latitude, a.longitude
FROM employers e
LEFT JOIN emp_agg  ea  ON ea.employer_id = e.employer_id
LEFT JOIN org_self os  ON os.name = e.name
LEFT JOIN org_addr oad ON oad.name = e.name
LEFT JOIN addresses a  ON a.address_id = COALESCE(e.address_id, oad.org_address_id);


-- ----------------------------------------------------------
-- 11. Permissions
-- ----------------------------------------------------------
-- Loader grants read access.


-- ----------------------------------------------------------
-- 12. Object documentation  (COMMENT ON -> stored in the DB)
-- ----------------------------------------------------------
-- The rich `--` notes above are source-only (visible just when reading this
-- file). These COMMENTs live IN the database, so DBeaver / pgAdmin / psql \d+
-- all show the purpose of each table and the intent behind the non-obvious
-- columns.

-- Tables
COMMENT ON TABLE occupation_categories IS 'Lookup: ~29 standardized occupation buckets. LAWYER vs ATTORNEY stay distinct as raw occupations; this groups them for filtering.';
COMMENT ON TABLE committees            IS 'FEC-registered committees + related orgs (AIPAC, ZOA, …). committee_number is the FEC id; committee_id is a surrogate PK.';
COMMENT ON TABLE donors                IS 'Unique donors (individuals + contributing orgs). One row per donor_key identity hash. No denormalized "latest" pointers — derived via views.';
COMMENT ON TABLE addresses             IS 'Shared address dimension — one source of truth for donor and employer locations, geocoded once.';
COMMENT ON TABLE employers             IS 'Unique companies. address_id is the default known location; donor_employments may select a closer office.';
COMMENT ON TABLE donor_addresses       IS 'Link: donor ↔ address. The "when" (first/last donation here) is NOT stored — it is derived from contributions (MIN/MAX receipt_date).';
COMMENT ON TABLE donor_employments     IS 'Link: each distinct (donor, employer, occupation). employer_id is NULL for retired / self-employed / not-employed / committee donors.';
COMMENT ON TABLE contributions         IS 'Fact table — one row per FEC filing. Foreign keys only, no denormalized copies. The largest table.';
COMMENT ON TABLE key_accomplices       IS 'Curated editorial content for the dashboard cards (sign/subtitle/body/image/order). Identity comes from donor_id → donors; NOT derived from FEC data.';
COMMENT ON TABLE leaders               IS 'Curated roster of org leadership (AIPAC / DMFI / …). Identity from donor_id → donors; committee memberships in leader_committees. NOT derived from FEC data.';
COMMENT ON TABLE leader_committees     IS 'Junction (M:N): which committees each leader sits on. Composite PK (leader_id, committee_id); both FKs enforced.';
COMMENT ON TABLE us_states             IS 'Reference: FIPS ↔ USPS code ↔ name. Standalone — no FK to the core schema.';
COMMENT ON TABLE zcta_state_rel        IS 'Reference: ZCTA (zip) → state FIPS. Standalone — no FK to the core schema.';
COMMENT ON TABLE zip_centroids         IS 'Reference: zip → lat/lng centroid (geocoding fallback). Standalone — no FK to the core schema.';

-- Columns that are not self-explanatory from name + type
COMMENT ON COLUMN contributions.sub_id IS 'FEC''s own numeric filing id (the natural key) — kept as the PK name on purpose, NOT a surrogate "id".';
COMMENT ON COLUMN committees.committee_id IS 'Surrogate integer PK. The real FEC identifier is committee_number.';
COMMENT ON COLUMN donor_employments.previous_employer_id IS 'References employers.employer_id — the donor''s PRIOR employer (e.g. "Retired · previously at X"). It is an EMPLOYER, not a previous donor_employments row.';
COMMENT ON COLUMN donor_employments.employer_status IS 'One of: active / retired / self_employed / not_employed / committee / missing.';
COMMENT ON COLUMN donors.donor_key IS 'Stable identity hash from donor_match — used in public URLs (/donor/<key>) and survives DB reloads.';
COMMENT ON COLUMN key_accomplices.sign IS 'Deck-of-cards id for the landing-page visualization (e.g. "Ace Spades", "Joker"). Nullable.';
COMMENT ON COLUMN key_accomplices.display_order IS 'Curator-defined card ordering from the source CSV.';
