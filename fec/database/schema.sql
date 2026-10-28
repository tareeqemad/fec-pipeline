-- FEC database v1
-- PostgreSQL 18+; pg_trgm, cube, earthdistance.

-- Occupation categories
CREATE TABLE occupation_categories (
    occupation_category_id  SERIAL  PRIMARY KEY,
    name    TEXT    NOT NULL UNIQUE
);

-- Committees and organizations
CREATE TABLE committees (
    committee_id        SERIAL          PRIMARY KEY,
    committee_number    TEXT            UNIQUE,
    committee_name      TEXT            NOT NULL,
    committee_short     TEXT,
    raised              NUMERIC(15, 2)  CHECK (raised >= 0),
    spent               NUMERIC(15, 2)  CHECK (spent  >= 0),
    irs_990_link        TEXT,
    logo_path           TEXT
);

-- Donors
CREATE TABLE donors (
    donor_id        SERIAL  PRIMARY KEY,
    donor_key       TEXT    NOT NULL UNIQUE,
    entity_type     TEXT    NOT NULL CHECK (entity_type IN ('INDIVIDUAL', 'ORGANIZATION', 'COMMITTEE/PAC')),
    first_name      TEXT,
    last_name       TEXT,
    identity_status TEXT    NOT NULL DEFAULT 'confirmed'
                    CHECK (identity_status IN ('confirmed', 'held', 'unresolved'))
);

-- Donor and company addresses
CREATE TABLE addresses (
    address_id  SERIAL              PRIMARY KEY,
    street_1    TEXT,
    street_2    TEXT,
    city        TEXT,
    state_code  TEXT,
    zip_code    TEXT,
    latitude    DOUBLE PRECISION    CHECK (latitude  BETWEEN  -90 AND  90),
    longitude   DOUBLE PRECISION    CHECK (longitude BETWEEN -180 AND 180)
);

-- Companies
CREATE TABLE employers (
    employer_id SERIAL  PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    address_id  INT     REFERENCES addresses(address_id)
);

-- Donor addresses
CREATE TABLE donor_addresses (
    donor_address_id    SERIAL              PRIMARY KEY,
    donor_id            INT                 NOT NULL REFERENCES donors(donor_id),
    address_id          INT                 NOT NULL REFERENCES addresses(address_id)
);

-- Donor employment
CREATE TABLE donor_employments (
    donor_employment_id     SERIAL              PRIMARY KEY,
    donor_id                INT                 NOT NULL REFERENCES donors(donor_id),
    employer_id             INT                 REFERENCES employers(employer_id),
    occupation              TEXT,
    occupation_category_id  INT                 REFERENCES occupation_categories(occupation_category_id),
    employer_status         TEXT                CHECK (employer_status IN (
                                                    'active', 'retired', 'self_employed',
                                                    'not_employed', 'committee',
                                                    'organization', 'missing')),
    previous_employer_id    INT                 REFERENCES employers(employer_id),
    -- Previous self-employment, for retired donors.
    previous_self_employed  BOOLEAN             NOT NULL DEFAULT FALSE,
    -- Reported self-employed address or inferred company office.
    address_id              INT                 REFERENCES addresses(address_id),
    CONSTRAINT donor_employments_previous_only_retired CHECK (
        employer_status = 'retired'
        OR (previous_employer_id IS NULL AND NOT previous_self_employed)
    ),
    CONSTRAINT donor_employments_previous_one_kind CHECK (
        NOT (previous_self_employed AND previous_employer_id IS NOT NULL)
    ),
    -- Keep each filing's employment details separate.
    UNIQUE NULLS NOT DISTINCT (donor_id, employer_id, occupation, employer_status,
                               previous_employer_id, previous_self_employed, occupation_category_id)
);

-- Contributions
CREATE TABLE contributions (
    sub_id              BIGINT          PRIMARY KEY,
    transaction_id      TEXT            NOT NULL,
    donor_id            INT             NOT NULL REFERENCES donors(donor_id),
    committee_id        INT             NOT NULL REFERENCES committees(committee_id),
    donor_address_id    INT             REFERENCES donor_addresses(donor_address_id),
    donor_employment_id INT             REFERENCES donor_employments(donor_employment_id),
    amount              NUMERIC(15, 2)  NOT NULL,
    receipt_date        DATE            NOT NULL,
    election_cycle      INTEGER         NOT NULL
);

-- Indexes
CREATE INDEX idx_donors_entity      ON donors (entity_type);
CREATE INDEX idx_donors_last        ON donors (last_name);
CREATE INDEX idx_donors_first_last  ON donors (last_name, first_name);
CREATE INDEX idx_donors_name_trgm   ON donors USING gin (last_name gin_trgm_ops);

CREATE INDEX idx_addr_state         ON addresses (state_code);
CREATE INDEX idx_addr_zip           ON addresses (zip_code);
CREATE INDEX idx_addr_city          ON addresses (city);
CREATE INDEX idx_addr_geo           ON addresses (latitude, longitude)
    WHERE latitude IS NOT NULL;

CREATE INDEX idx_da_donor           ON donor_addresses (donor_id);
CREATE INDEX idx_da_address         ON donor_addresses (address_id);

CREATE INDEX idx_de_donor           ON donor_employments (donor_id);
CREATE INDEX idx_de_employer        ON donor_employments (employer_id);
CREATE INDEX idx_de_occ_cat         ON donor_employments (occupation_category_id);
CREATE INDEX idx_de_prev_employer   ON donor_employments (previous_employer_id);

CREATE INDEX idx_c_donor            ON contributions (donor_id);
CREATE INDEX idx_c_committee        ON contributions (committee_id);
CREATE INDEX idx_c_date             ON contributions (receipt_date);
CREATE INDEX idx_c_cycle            ON contributions (election_cycle);
CREATE INDEX idx_c_donor_address    ON contributions (donor_address_id);
CREATE INDEX idx_c_donor_employment ON contributions (donor_employment_id);
CREATE INDEX idx_c_donor_date       ON contributions (donor_id, receipt_date DESC);

CREATE INDEX idx_emp_name           ON employers (name);
CREATE INDEX idx_emp_name_trgm      ON employers USING gin (name gin_trgm_ops);
CREATE INDEX idx_emp_address        ON employers (address_id);

-- ZIP centres; the donor profile view falls back to them
CREATE TABLE zip_centroids (
    zip         TEXT                NOT NULL PRIMARY KEY,
    lat         DOUBLE PRECISION    NOT NULL CHECK (lat BETWEEN  -90 AND  90),
    lng         DOUBLE PRECISION    NOT NULL CHECK (lng BETWEEN -180 AND 180),
    source      TEXT                NOT NULL DEFAULT 'census_gazetteer_2023',
    fetched_at  TIMESTAMPTZ         DEFAULT NOW()
);
CREATE INDEX idx_zip_centroids_geo ON zip_centroids (lat, lng);

-- Latest donor address
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
    c.sub_id       DESC;

-- Latest donor employment
CREATE OR REPLACE VIEW v_donor_current_employment AS
SELECT DISTINCT ON (c.donor_id)
    c.donor_id,
    e.donor_employment_id,
    e.employer_id,
    e.occupation,
    e.occupation_category_id,
    e.employer_status,
    e.previous_employer_id,
    e.previous_self_employed,
    e.address_id
FROM contributions c
JOIN donor_employments e ON e.donor_employment_id = c.donor_employment_id
ORDER BY
    c.donor_id,
    c.receipt_date DESC,
    c.sub_id       DESC;

-- Donor totals
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

-- Donor profiles
CREATE OR REPLACE VIEW v_donor_profile AS
SELECT
    d.donor_id,
    d.donor_key,
    d.entity_type,
    d.first_name,
    d.last_name,
    d.identity_status,
    s.donation_count,
    s.total_amount,
    s.avg_amount,
    s.first_donation,
    s.last_donation,
    s.election_cycles,
    a.street_1      AS current_street,
    a.street_2      AS current_street_2,
    a.city          AS current_city,
    a.state_code    AS current_state,
    a.zip_code      AS current_zip,
    -- Ungeocoded addresses (PO boxes) fall back to their ZIP.
    COALESCE(a.latitude, zc.lat)  AS current_lat,
    COALESCE(a.longitude, zc.lng) AS current_lng,
    emp.name        AS current_employer,
    e.occupation    AS current_occupation,
    oc.name         AS current_occ_category,
    e.employer_status,
    COALESCE(
        prev.name,
        CASE WHEN e.previous_self_employed THEN 'SELF-EMPLOYED' END
    )               AS previous_employer
FROM donors d
LEFT JOIN v_donor_stats              s    ON s.donor_id   = d.donor_id
LEFT JOIN v_donor_current_address    a    ON a.donor_id   = d.donor_id
LEFT JOIN zip_centroids              zc   ON zc.zip       = left(a.zip_code, 5)
LEFT JOIN v_donor_current_employment e    ON e.donor_id   = d.donor_id
LEFT JOIN employers                  emp  ON emp.employer_id           = e.employer_id
LEFT JOIN occupation_categories      oc   ON oc.occupation_category_id = e.occupation_category_id
LEFT JOIN employers                  prev ON prev.employer_id          = e.previous_employer_id;

-- Cached donor profiles
CREATE MATERIALIZED VIEW mv_donor_profile AS
SELECT * FROM v_donor_profile;

CREATE UNIQUE INDEX idx_mvdp_id     ON mv_donor_profile (donor_id);
CREATE INDEX idx_mvdp_key           ON mv_donor_profile (donor_key);
CREATE INDEX idx_mvdp_entity        ON mv_donor_profile (entity_type);
CREATE INDEX idx_mvdp_total         ON mv_donor_profile (total_amount DESC NULLS LAST);
CREATE INDEX idx_mvdp_state         ON mv_donor_profile (current_state);
CREATE INDEX idx_mvdp_state_total   ON mv_donor_profile (current_state, total_amount DESC NULLS LAST);
CREATE INDEX idx_mvdp_name_trgm     ON mv_donor_profile USING gin (last_name gin_trgm_ops);
CREATE INDEX idx_mvdp_first_name_trgm ON mv_donor_profile USING gin (first_name gin_trgm_ops);
CREATE INDEX idx_mvdp_employer      ON mv_donor_profile (current_employer);
CREATE INDEX idx_mvdp_prev_employer ON mv_donor_profile (previous_employer);
CREATE INDEX idx_mvdp_occ           ON mv_donor_profile (current_occ_category);
CREATE INDEX idx_mvdp_geo           ON mv_donor_profile (current_lat, current_lng)
    WHERE current_lat IS NOT NULL;
CREATE INDEX idx_mvdp_earth         ON mv_donor_profile USING gist (ll_to_earth(current_lat, current_lng));

-- Reference tables
CREATE TABLE us_states (
    state_fips  TEXT    NOT NULL PRIMARY KEY,
    code        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL
);

CREATE TABLE zcta_state_rel (
    zcta5       TEXT    NOT NULL PRIMARY KEY,
    state_fips  TEXT    NOT NULL REFERENCES us_states(state_fips)
);

-- Curated donor cards
CREATE TABLE key_accomplices (
    accomplice_id   SERIAL          PRIMARY KEY,
    donor_id        INTEGER         NOT NULL UNIQUE REFERENCES donors(donor_id),
    sign            TEXT,
    subtitle        TEXT,
    body_text       TEXT,
    committee_id    INTEGER         REFERENCES committees(committee_id),
    display_order   INTEGER         NOT NULL DEFAULT 0
);

-- Donor portraits
CREATE TABLE donor_images (
    donor_id   INTEGER PRIMARY KEY REFERENCES donors(donor_id) ON DELETE CASCADE,
    image_path TEXT NOT NULL
);
CREATE INDEX idx_key_accomplices_sign  ON key_accomplices (sign) WHERE sign IS NOT NULL;
CREATE INDEX idx_key_accomplices_order ON key_accomplices (display_order);

-- Leaders
CREATE TABLE leaders (
    leader_id                   SERIAL              PRIMARY KEY,
    donor_id                    INTEGER             NOT NULL UNIQUE REFERENCES donors(donor_id)
);

-- Leader committee memberships
CREATE TABLE leader_committees (
    leader_id       INTEGER     NOT NULL REFERENCES leaders(leader_id) ON DELETE CASCADE,
    committee_id    INTEGER     NOT NULL REFERENCES committees(committee_id),
    PRIMARY KEY (leader_id, committee_id)
);

CREATE INDEX idx_lc_committee ON leader_committees (committee_id);

-- Database descriptions
COMMENT ON COLUMN donors.identity_status IS 'confirmed = no identity flag; held = kept separate for review; unresolved = unknown donor behind a network name. These labels do not prove identity.';

COMMENT ON TABLE occupation_categories IS 'Occupation groups.';
COMMENT ON TABLE committees            IS 'Committees and related organizations.';
COMMENT ON TABLE donors                IS 'Donor identities.';
COMMENT ON TABLE addresses             IS 'Shared donor and company addresses.';
COMMENT ON TABLE employers             IS 'Companies and their default addresses.';
COMMENT ON TABLE donor_addresses       IS 'Links between donors and addresses.';
COMMENT ON TABLE donor_employments     IS 'Donor employment details and linked addresses.';
COMMENT ON TABLE contributions         IS 'Contribution records linked to donors, committees, addresses and employment.';
COMMENT ON TABLE key_accomplices       IS 'Curated donor card content.';
COMMENT ON TABLE leaders               IS 'Curated organization leaders.';
COMMENT ON TABLE leader_committees     IS 'Leader committee memberships.';
COMMENT ON TABLE us_states             IS 'State FIPS codes, postal codes and names.';
COMMENT ON TABLE zcta_state_rel        IS 'ZIP Code Tabulation Areas and state FIPS codes.';
COMMENT ON TABLE zip_centroids         IS 'ZIP coordinates for geocoding fallback.';

COMMENT ON COLUMN contributions.sub_id IS 'FEC record identifier.';
COMMENT ON COLUMN committees.committee_id IS 'Internal committee identifier.';
COMMENT ON COLUMN donor_employments.previous_employer_id IS 'Previous company for a retired donor.';
COMMENT ON COLUMN donor_employments.employer_status IS 'Employment status for this record.';
COMMENT ON COLUMN donor_employments.previous_self_employed IS 'Previous self-employment for retired donors.';
COMMENT ON COLUMN donors.donor_key IS 'Donor identity key from the matching process.';
COMMENT ON COLUMN key_accomplices.sign IS 'Card symbol.';
COMMENT ON COLUMN key_accomplices.display_order IS 'Card display order.';
