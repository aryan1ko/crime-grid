"""
pipeline/04_load.py

Loads all processed ontology objects into DuckDB.

Schema mirrors the ontology object model:
  - One table per Object Type
  - One table per Link Type (relationship)
  - Properties stored as columns with documented provenance

DuckDB is used here instead of Postgres because:
  - Zero infrastructure (file-based, embedded)
  - Full SQL + spatial extension support
  - Parquet-native (reads our processed files directly)
  - Fast enough for 2.5M records on a laptop
"""

import os
import duckdb
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROCESSED_DIR = Path("data/processed")
DB_PATH = os.getenv("DB_PATH", "data/austin_crime.duckdb")


def get_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(DB_PATH)
    con.execute("INSTALL spatial; LOAD spatial;")
    return con


def create_schema(con: duckdb.DuckDBPyConnection):
    """
    Create ontology-aligned schema.
    
    Design note: We use explicit object type tables rather than a single
    flat facts table. This mirrors how Foundry's ontology layer separates
    object types from their properties and links.
    """
    print("  Creating schema...")

    con.execute("""
    -- ─────────────────────────────────────────────
    -- OBJECT TYPE: CensusTract
    -- ─────────────────────────────────────────────
    CREATE OR REPLACE TABLE census_tract (
        geoid               VARCHAR PRIMARY KEY,   -- 11-digit FIPS
        name                VARCHAR,
        total_population    INTEGER,
        median_household_income DOUBLE,
        poverty_rate        DOUBLE,
        renter_rate         DOUBLE,
        pct_white           DOUBLE,
        pct_black           DOUBLE,
        pct_hispanic        DOUBLE,
        median_age          DOUBLE,
        -- Provenance
        data_source         VARCHAR DEFAULT 'ACS 5-Year 2019',
        load_timestamp      TIMESTAMP DEFAULT current_timestamp
    );

    -- ─────────────────────────────────────────────
    -- OBJECT TYPE: Location
    -- ─────────────────────────────────────────────
    CREATE OR REPLACE TABLE location (
        location_id         VARCHAR PRIMARY KEY,   -- MD5 of normalized address
        normalized_address  VARCHAR NOT NULL,
        canonical_lat       DOUBLE,
        canonical_lon       DOUBLE,
        incident_count      INTEGER,               -- denormalized count for perf
        -- Link to CensusTract (materialized FK)
        tract_geoid         VARCHAR REFERENCES census_tract(geoid),
        -- Provenance
        data_source         VARCHAR DEFAULT 'Austin PD Crime Reports (address centroid)',
        load_timestamp      TIMESTAMP DEFAULT current_timestamp
    );

    -- ─────────────────────────────────────────────
    -- OBJECT TYPE: OffenseType
    -- ─────────────────────────────────────────────
    CREATE OR REPLACE TABLE offense_type (
        offense_type_id     INTEGER PRIMARY KEY,
        raw_description     VARCHAR NOT NULL,      -- Austin PD original string
        ucr_category        VARCHAR NOT NULL,      -- Normalized UCR category
        is_violent          BOOLEAN NOT NULL,
        load_timestamp      TIMESTAMP DEFAULT current_timestamp
    );

    -- ─────────────────────────────────────────────
    -- OBJECT TYPE: Incident
    -- ─────────────────────────────────────────────
    CREATE OR REPLACE TABLE incident (
        incident_report_number  VARCHAR PRIMARY KEY,
        occurred_dt             TIMESTAMP,
        report_dt               TIMESTAMP,
        hour_of_day             INTEGER,
        day_of_week             INTEGER,           -- 0=Monday
        month                   INTEGER,
        year                    INTEGER,
        is_violent              BOOLEAN,
        clearance_status        VARCHAR,
        clearance_date          VARCHAR,
        council_district        VARCHAR,
        apd_sector              VARCHAR,
        apd_district            VARCHAR,
        -- Links (materialized FKs)
        location_id             VARCHAR REFERENCES location(location_id),
        offense_type_id         INTEGER REFERENCES offense_type(offense_type_id),
        tract_geoid             VARCHAR REFERENCES census_tract(geoid),
        -- Provenance
        data_source             VARCHAR DEFAULT 'Austin PD Crime Reports',
        load_timestamp          TIMESTAMP DEFAULT current_timestamp
    );

    -- ─────────────────────────────────────────────
    -- LINK TYPE: incident_location (explicit link table)
    -- Redundant with FK above but useful for Foundry-style link queries
    -- ─────────────────────────────────────────────
    CREATE OR REPLACE TABLE link_incident_location (
        incident_report_number  VARCHAR,
        location_id             VARCHAR,
        link_type               VARCHAR DEFAULT 'occurs_at'
    );

    CREATE OR REPLACE TABLE link_location_tract (
        location_id     VARCHAR,
        tract_geoid     VARCHAR,
        link_type       VARCHAR DEFAULT 'within'
    );
    """)
    print("  Schema created.")


def load_census_tracts(con: duckdb.DuckDBPyConnection):
    print("  Loading CensusTract objects...")
    df = pd.read_parquet(PROCESSED_DIR / "census_tracts_raw.parquet")
    df = df.rename(columns={"name": "tract_name"})

    # Select only schema columns
    cols = [
        "geoid", "total_population", "median_household_income",
        "poverty_rate", "renter_rate", "pct_white", "pct_black",
        "pct_hispanic", "median_age"
    ]
    df = df[cols].drop_duplicates(subset=["geoid"])
    df["name"] = df["geoid"]  # fallback name

    con.execute("INSERT OR REPLACE INTO census_tract SELECT * FROM df")
    count = con.execute("SELECT COUNT(*) FROM census_tract").fetchone()[0]
    print(f"    Loaded {count:,} census tracts")


def load_locations(con: duckdb.DuckDBPyConnection):
    print("  Loading Location objects...")
    df = pd.read_parquet(PROCESSED_DIR / "locations_enriched.parquet")

    cols = ["location_id", "normalized_address", "canonical_lat", "canonical_lon",
            "incident_count", "tract_geoid"]
    df = df[[c for c in cols if c in df.columns]]

    con.register("locations_df", df)
    con.execute("INSERT OR REPLACE INTO location SELECT * FROM locations_df")
    count = con.execute("SELECT COUNT(*) FROM location").fetchone()[0]
    print(f"    Loaded {count:,} locations")


def load_offense_types(con: duckdb.DuckDBPyConnection):
    print("  Loading OffenseType objects...")
    df = pd.read_parquet(PROCESSED_DIR / "offense_types.parquet")
    cols = ["offense_type_id", "highest_offense_description", "ucr_category", "is_violent"]
    df = df.rename(columns={"highest_offense_description": "raw_description"})
    con.register("offense_df", df[["offense_type_id", "raw_description", "ucr_category", "is_violent"]])
    con.execute("INSERT OR REPLACE INTO offense_type SELECT * FROM offense_df")
    count = con.execute("SELECT COUNT(*) FROM offense_type").fetchone()[0]
    print(f"    Loaded {count:,} offense types")


def load_incidents(con: duckdb.DuckDBPyConnection):
    print("  Loading Incident objects...")
    df = pd.read_parquet(PROCESSED_DIR / "incidents_enriched.parquet")

    # Join to get offense_type_id
    offense_df = pd.read_parquet(PROCESSED_DIR / "offense_types.parquet")
    df = df.merge(
        offense_df[["highest_offense_description", "offense_type_id"]],
        on="highest_offense_description",
        how="left"
    )

    # Join to get location_id
    locations_df = pd.read_parquet(PROCESSED_DIR / "locations_enriched.parquet")
    df = df.merge(
        locations_df[["normalized_address", "location_id"]],
        on="normalized_address",
        how="left"
    )

    incident_cols = [
        "incident_report_number", "occurred_dt", "report_dt",
        "hour_of_day", "day_of_week", "month", "year",
        "is_violent", "clearance_status", "clearance_date",
        "council_district", "apd_sector", "apd_district",
        "location_id", "offense_type_id", "census_tract_final"
    ]
    df = df.rename(columns={"census_tract_final": "tract_geoid"})
    df = df[[c for c in incident_cols if c in df.columns]]
    df = df.drop_duplicates(subset=["incident_report_number"])

    con.register("incidents_df", df)
    con.execute("INSERT OR REPLACE INTO incident SELECT * FROM incidents_df")
    count = con.execute("SELECT COUNT(*) FROM incident").fetchone()[0]
    print(f"    Loaded {count:,} incidents")


def build_link_tables(con: duckdb.DuckDBPyConnection):
    print("  Building link tables...")
    con.execute("""
        INSERT INTO link_incident_location
        SELECT incident_report_number, location_id, 'occurs_at'
        FROM incident WHERE location_id IS NOT NULL;
    """)
    con.execute("""
        INSERT INTO link_location_tract
        SELECT location_id, tract_geoid, 'within'
        FROM location WHERE tract_geoid IS NOT NULL;
    """)
    print("  Link tables populated.")


def validate(con: duckdb.DuckDBPyConnection):
    print("\n  Validation:")
    tables = ["census_tract", "location", "offense_type", "incident",
              "link_incident_location", "link_location_tract"]
    for t in tables:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"    {t}: {n:,} rows")

    # Referential integrity spot checks
    orphaned = con.execute("""
        SELECT COUNT(*) FROM incident i
        LEFT JOIN location l ON i.location_id = l.location_id
        WHERE i.location_id IS NOT NULL AND l.location_id IS NULL
    """).fetchone()[0]
    print(f"    Orphaned incident→location links: {orphaned}")


if __name__ == "__main__":
    print("=" * 60)
    print("Austin Crime Ontology — Step 4: Load")
    print("=" * 60)

    con = get_connection()
    create_schema(con)
    load_census_tracts(con)
    load_locations(con)
    load_offense_types(con)
    load_incidents(con)
    build_link_tables(con)
    validate(con)
    con.close()

    print(f"\nDatabase written to: {DB_PATH}")
    print("Run analysis/queries.sql or analysis/hotspot.py next.")
