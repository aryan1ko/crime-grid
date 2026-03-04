-- ontology/schema.sql
-- 
-- Austin Crime Ontology — Canonical Schema
-- 
-- This file documents the full ontology schema independently of the
-- pipeline load scripts. Read this alongside design_doc.md.
--
-- Object Types: census_tract, location, offense_type, incident
-- Link Types:   link_incident_location, link_location_tract
-- ──────────────────────────────────────────────────────────────────

-- ══════════════════════════════════════════════════════════════════
-- OBJECT TYPE: CensusTract
--
-- Represents a US Census geographic unit within Travis County, TX.
-- Properties sourced from ACS 5-Year 2019 estimates.
-- This is a slowly-changing object — we do not model temporal
-- changes in demographics (a real Foundry ontology would use
-- time-series branches or versioned properties).
-- ══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS census_tract (
    geoid                   VARCHAR(11) PRIMARY KEY,  -- Standard 11-digit FIPS
    name                    VARCHAR,
    total_population        INTEGER,
    median_household_income DOUBLE,
    poverty_rate            DOUBLE,     -- poverty_count / total_population
    renter_rate             DOUBLE,     -- renter_occupied / total_housing_units
    pct_white               DOUBLE,
    pct_black               DOUBLE,
    pct_hispanic            DOUBLE,
    median_age              DOUBLE,
    data_source             VARCHAR,
    load_timestamp          TIMESTAMP
);

-- ══════════════════════════════════════════════════════════════════
-- OBJECT TYPE: Location
--
-- Represents a unique physical location where incidents occur.
-- Resolution unit: street block (Austin PD reports block-level addresses).
-- canonical_lat/lon is the centroid of all reported coordinates
-- for incidents at this normalized address.
--
-- Design decision: We do NOT model individual building addresses
-- because APD anonymizes to block level. Attempting finer resolution
-- would create false precision.
-- ══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS location (
    location_id             VARCHAR(12) PRIMARY KEY,  -- MD5 hash of normalized address
    normalized_address      VARCHAR NOT NULL,
    canonical_lat           DOUBLE,
    canonical_lon           DOUBLE,
    incident_count          INTEGER,
    tract_geoid             VARCHAR(11) REFERENCES census_tract(geoid),
    data_source             VARCHAR,
    load_timestamp          TIMESTAMP
);

-- ══════════════════════════════════════════════════════════════════
-- OBJECT TYPE: OffenseType
--
-- Normalized crime category. Two levels:
--   raw_description: Austin PD's original offense string
--   ucr_category: Normalized UCR Uniform Crime Report category
--
-- Design decision: We keep both levels. raw_description preserves
-- Austin-specific detail (e.g., "Burglary of Vehicle" vs "Burglary").
-- ucr_category enables cross-jurisdiction comparisons.
-- is_violent follows Part 1 Violent Crime UCR definition.
-- ══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS offense_type (
    offense_type_id         INTEGER PRIMARY KEY,
    raw_description         VARCHAR NOT NULL,
    ucr_category            VARCHAR NOT NULL,
    is_violent              BOOLEAN NOT NULL,
    load_timestamp          TIMESTAMP
);

-- ══════════════════════════════════════════════════════════════════
-- OBJECT TYPE: Incident
--
-- A single reported crime event, uniquely identified by
-- incident_report_number (Austin PD's case number).
--
-- Design decision: one incident = one incident_report_number.
-- APD may report multiple offenses per incident; we use the
-- "highest offense" as the primary classification. A full ontology
-- would model a separate Charge object type with many-to-one
-- relationship to Incident.
--
-- Temporal properties (hour_of_day, day_of_week, month, year) are
-- derived from occurred_dt at load time for query performance.
-- ══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS incident (
    incident_report_number  VARCHAR PRIMARY KEY,
    occurred_dt             TIMESTAMP,
    report_dt               TIMESTAMP,
    hour_of_day             INTEGER,    -- 0-23
    day_of_week             INTEGER,    -- 0=Monday, 6=Sunday
    month                   INTEGER,    -- 1-12
    year                    INTEGER,
    is_violent              BOOLEAN,
    clearance_status        VARCHAR,
    clearance_date          VARCHAR,
    council_district        VARCHAR,
    apd_sector              VARCHAR,
    apd_district            VARCHAR,
    -- Links (materialized as FKs for query performance)
    location_id             VARCHAR(12) REFERENCES location(location_id),
    offense_type_id         INTEGER REFERENCES offense_type(offense_type_id),
    tract_geoid             VARCHAR(11) REFERENCES census_tract(geoid),
    data_source             VARCHAR,
    load_timestamp          TIMESTAMP
);

-- ══════════════════════════════════════════════════════════════════
-- LINK TYPE: Incident → occurs_at → Location
-- LINK TYPE: Location → within → CensusTract
--
-- Explicit link tables mirror Foundry's link type model.
-- The FKs in the object tables above are a materialized form of
-- these links for query convenience. In a true Foundry ontology,
-- links would be first-class objects and would not be denormalized
-- into the object tables.
-- ══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS link_incident_location (
    incident_report_number  VARCHAR REFERENCES incident(incident_report_number),
    location_id             VARCHAR(12) REFERENCES location(location_id),
    link_type               VARCHAR DEFAULT 'occurs_at',
    PRIMARY KEY (incident_report_number, location_id)
);

CREATE TABLE IF NOT EXISTS link_location_tract (
    location_id             VARCHAR(12) REFERENCES location(location_id),
    tract_geoid             VARCHAR(11) REFERENCES census_tract(geoid),
    link_type               VARCHAR DEFAULT 'within',
    PRIMARY KEY (location_id, tract_geoid)
);
