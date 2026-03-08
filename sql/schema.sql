-- sql/schema.sql
-- DuckDB schema for the Austin Crime Ontology
-- Run via: duckdb data/austin_crime.duckdb < sql/schema.sql

-- ─────────────────────────────────────────────────────────────────────────────
-- OBJECT TYPE: Location
-- Represents a unique physical address/coordinate pair.
-- Entity resolution deduplicates raw addresses into canonical locations.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS obj_location (
    location_id         VARCHAR PRIMARY KEY,   -- SHA1 of normalized address
    raw_address         VARCHAR,
    normalized_address  VARCHAR,
    latitude            DOUBLE NOT NULL,
    longitude           DOUBLE NOT NULL,
    zip_code            INTEGER,
    census_tract_geoid  VARCHAR,               -- FK → obj_census_tract.geoid
    apd_district        VARCHAR,               -- FK → obj_district.district_id
    incident_count      INTEGER DEFAULT 0,     -- denormalized for performance
    is_hotspot          BOOLEAN DEFAULT FALSE,
    first_seen          TIMESTAMP,
    last_seen           TIMESTAMP,
    -- Provenance
    source              VARCHAR DEFAULT 'austin_apd',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- OBJECT TYPE: Incident
-- A single reported crime event.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS obj_incident (
    incident_id             VARCHAR PRIMARY KEY,  -- incident_report_number
    location_id             VARCHAR,              -- FK → obj_location
    offense_type_id         VARCHAR,              -- FK → obj_offense_type
    district_id             VARCHAR,              -- FK → obj_district

    occurred_at             TIMESTAMP,
    reported_at             TIMESTAMP,
    year                    INTEGER,
    month                   INTEGER,
    hour                    INTEGER,
    day_of_week             VARCHAR,

    clearance_status        VARCHAR,
    clearance_date          TIMESTAMP,

    is_violent              BOOLEAN,
    is_property             BOOLEAN,

    -- Provenance
    source                  VARCHAR DEFAULT 'austin_apd',
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- OBJECT TYPE: OffenseType
-- UCR/NIBRS normalized offense classification.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS obj_offense_type (
    offense_type_id     VARCHAR PRIMARY KEY,   -- slugified crime_type
    raw_crime_type      VARCHAR,
    ucr_category        VARCHAR,
    category_description VARCHAR,
    is_violent          BOOLEAN,
    is_property         BOOLEAN,
    incident_count      INTEGER DEFAULT 0      -- denormalized
);

-- ─────────────────────────────────────────────────────────────────────────────
-- OBJECT TYPE: District
-- APD patrol district.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS obj_district (
    district_id         VARCHAR PRIMARY KEY,
    district_name       VARCHAR,
    sector              VARCHAR,
    incident_count      INTEGER DEFAULT 0,
    geometry_wkt        VARCHAR   -- WKT polygon, for reference
);

-- ─────────────────────────────────────────────────────────────────────────────
-- OBJECT TYPE: CensusTract
-- Census geographic unit — links crime to demographic context.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS obj_census_tract (
    geoid               VARCHAR PRIMARY KEY,   -- 11-digit GEOID
    tract               VARCHAR,
    tract_name          VARCHAR,
    area_land_sqm       DOUBLE,
    area_water_sqm      DOUBLE,
    geometry_wkt        VARCHAR
);

-- ─────────────────────────────────────────────────────────────────────────────
-- OBJECT TYPE: Demographics
-- ACS 5-year estimates per census tract.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS obj_demographics (
    demo_id                 VARCHAR PRIMARY KEY,  -- geoid + '_' + year
    geoid                   VARCHAR,              -- FK → obj_census_tract
    acs_year                INTEGER,

    total_population        INTEGER,
    median_household_income INTEGER,
    population_below_poverty INTEGER,
    poverty_rate            DOUBLE,
    unemployment_rate       DOUBLE,
    median_home_value       INTEGER,

    population_white        INTEGER,
    population_black        INTEGER,
    population_hispanic     INTEGER,
    population_asian        INTEGER,

    pct_white               DOUBLE,
    pct_black               DOUBLE,
    pct_hispanic            DOUBLE,
    pct_asian               DOUBLE,

    source                  VARCHAR DEFAULT 'acs5',
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────────────────────────────────────
-- LINK TYPES
-- These tables represent directed relationships between object types.
-- ─────────────────────────────────────────────────────────────────────────────

-- Incident → occurred_at → Location
CREATE TABLE IF NOT EXISTS link_incident_location (
    incident_id     VARCHAR,
    location_id     VARCHAR,
    PRIMARY KEY (incident_id, location_id)
);

-- Incident → classified_as → OffenseType
CREATE TABLE IF NOT EXISTS link_incident_offense (
    incident_id     VARCHAR,
    offense_type_id VARCHAR,
    PRIMARY KEY (incident_id, offense_type_id)
);

-- Location → within → CensusTract  (spatial join result)
CREATE TABLE IF NOT EXISTS link_location_tract (
    location_id     VARCHAR,
    geoid           VARCHAR,
    join_method     VARCHAR,   -- 'spatial', 'census_field', 'manual'
    PRIMARY KEY (location_id, geoid)
);

-- CensusTract → enriched_by → Demographics
CREATE TABLE IF NOT EXISTS link_tract_demographics (
    geoid           VARCHAR,
    demo_id         VARCHAR,
    acs_year        INTEGER,
    PRIMARY KEY (geoid, acs_year)
);

-- Location → patrolled_by → District
CREATE TABLE IF NOT EXISTS link_location_district (
    location_id     VARCHAR,
    district_id     VARCHAR,
    PRIMARY KEY (location_id, district_id)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- ANALYTICAL VIEWS
-- Pre-built cross-object views for the analytical layer.
-- ─────────────────────────────────────────────────────────────────────────────

-- Full incident context: one row per incident with all joined attributes
CREATE OR REPLACE VIEW vw_incident_full AS
SELECT
    i.incident_id,
    i.occurred_at,
    i.year,
    i.month,
    i.hour,
    i.day_of_week,
    i.clearance_status,
    i.is_violent,
    i.is_property,

    l.normalized_address,
    l.latitude,
    l.longitude,
    l.zip_code,
    l.is_hotspot,

    o.ucr_category,
    o.category_description,

    d.district_name,
    d.sector,

    t.tract,
    t.tract_name,

    dm.total_population,
    dm.median_household_income,
    dm.poverty_rate,
    dm.pct_white,
    dm.pct_black,
    dm.pct_hispanic

FROM obj_incident i
LEFT JOIN obj_location l ON i.location_id = l.location_id
LEFT JOIN obj_offense_type o ON i.offense_type_id = o.offense_type_id
LEFT JOIN obj_district d ON i.district_id = d.district_id
LEFT JOIN link_location_tract llt ON l.location_id = llt.location_id
LEFT JOIN obj_census_tract t ON llt.geoid = t.geoid
LEFT JOIN link_tract_demographics ltd ON t.geoid = ltd.geoid
LEFT JOIN obj_demographics dm ON ltd.demo_id = dm.demo_id;

-- Tract-level crime summary for map visualization
CREATE OR REPLACE VIEW vw_tract_crime_summary AS
SELECT
    t.geoid,
    t.tract_name,
    COUNT(i.incident_id) AS total_incidents,
    SUM(i.is_violent::INT) AS violent_incidents,
    SUM(i.is_property::INT) AS property_incidents,
    dm.total_population,
    dm.median_household_income,
    dm.poverty_rate,
    CASE
        WHEN dm.total_population > 0
        THEN COUNT(i.incident_id)::DOUBLE / dm.total_population * 1000
        ELSE NULL
    END AS incidents_per_1k_pop
FROM obj_census_tract t
LEFT JOIN link_location_tract llt ON t.geoid = llt.geoid
LEFT JOIN obj_location l ON llt.location_id = l.location_id
LEFT JOIN obj_incident i ON l.location_id = i.location_id
LEFT JOIN link_tract_demographics ltd ON t.geoid = ltd.geoid
LEFT JOIN obj_demographics dm ON ltd.demo_id = dm.demo_id
GROUP BY t.geoid, t.tract_name, dm.total_population, dm.median_household_income, dm.poverty_rate;
