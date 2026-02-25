"""
ontology/link_builder.py

Builds all link type tables that connect object types to each other.

Link types:
    - link_incident_location     (Incident → occurred_at → Location)
    - link_incident_offense      (Incident → classified_as → OffenseType)
    - link_location_tract        (Location → within → CensusTract, via spatial join)
    - link_tract_demographics    (CensusTract → enriched_by → Demographics)
    - link_location_district     (Location → patrolled_by → District)

The spatial join (location → census tract) is the most important step.
Strategy:
    1. Try the census_tract field from the raw APD data (fast, not always populated)
    2. Fall back to spatial point-in-polygon join using tract geometries
"""

import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

import pandas as pd
import geopandas as gpd
import duckdb
from loguru import logger
import config
from ontology.build_objects import slugify


def build_incident_location_links(conn: duckdb.DuckDBPyConnection):
    """Simple 1:1 link from incident to its location."""
    logger.info("Building link_incident_location...")
    conn.execute("""
        INSERT OR IGNORE INTO link_incident_location (incident_id, location_id)
        SELECT incident_id, location_id
        FROM obj_incident
        WHERE location_id IS NOT NULL
    """)
    n = conn.execute("SELECT COUNT(*) FROM link_incident_location").fetchone()[0]
    logger.success(f"link_incident_location: {n:,} links")


def build_incident_offense_links(conn: duckdb.DuckDBPyConnection):
    """Simple 1:1 link from incident to offense type."""
    logger.info("Building link_incident_offense...")
    conn.execute("""
        INSERT OR IGNORE INTO link_incident_offense (incident_id, offense_type_id)
        SELECT incident_id, offense_type_id
        FROM obj_incident
        WHERE offense_type_id IS NOT NULL
    """)
    n = conn.execute("SELECT COUNT(*) FROM link_incident_offense").fetchone()[0]
    logger.success(f"link_incident_offense: {n:,} links")


def build_location_district_links(conn: duckdb.DuckDBPyConnection):
    """Link locations to districts using the apd_district field from crime data."""
    logger.info("Building link_location_district...")
    loc_df = conn.execute(
        "SELECT location_id, apd_district FROM obj_location WHERE apd_district IS NOT NULL"
    ).df()
    if loc_df.empty:
        logger.warning("No location district values available")
        return

    loc_df["district_id"] = loc_df["apd_district"].apply(slugify)
    conn.register("loc_district_links", loc_df[["location_id", "district_id"]])
    conn.execute("""
        INSERT OR IGNORE INTO link_location_district (location_id, district_id)
        SELECT location_id, district_id
        FROM loc_district_links
    """)
    n = conn.execute("SELECT COUNT(*) FROM link_location_district").fetchone()[0]
    logger.success(f"link_location_district: {n:,} links")


def build_location_tract_links(conn: duckdb.DuckDBPyConnection):
    """
    Spatial join: assign each Location to a CensusTract.

    Strategy 1: Use census_tract field from APD data (already a tract number).
    Strategy 2: Point-in-polygon join using GeoPandas for locations without a tract field.

    Design decision: We prefer the APD-provided census_tract for speed, but
    validate and backfill with spatial join. This is documented as a known
    tradeoff — APD tract assignment may lag boundary updates.
    """
    logger.info("Building link_location_tract (spatial join)...")

    loc_df = conn.execute(
        "SELECT location_id, latitude, longitude, census_tract_geoid FROM obj_location"
    ).df()
    tract_df = conn.execute("SELECT geoid, tract, geometry_wkt FROM obj_census_tract").df()

    if len(tract_df) == 0:
        logger.warning("No census tract geometries available — skipping spatial join")
        return

    # Build GeoDataFrame for tracts
    from shapely import wkt
    tract_df["geometry"] = tract_df["geometry_wkt"].apply(
        lambda w: wkt.loads(w) if pd.notna(w) and w else None
    )
    tract_df = tract_df.dropna(subset=["geometry"])
    tracts_gdf = gpd.GeoDataFrame(tract_df, geometry="geometry", crs="EPSG:4326")

    # Build GeoDataFrame for locations
    locs_gdf = gpd.GeoDataFrame(
        loc_df,
        geometry=gpd.points_from_xy(loc_df["longitude"], loc_df["latitude"]),
        crs="EPSG:4326",
    )

    # Spatial join
    logger.info(f"Spatial join: {len(locs_gdf):,} points → {len(tracts_gdf)} polygons...")
    joined = gpd.sjoin(locs_gdf, tracts_gdf[["geoid", "geometry"]], how="left", predicate="within")

    # Build links table
    links = joined[["location_id", "geoid"]].dropna(subset=["geoid"]).copy()
    links["join_method"] = "spatial"

    # For locations that did not spatially match, use provided tract geoid when valid.
    unmatched = joined[joined["geoid"].isna()][["location_id", "census_tract_geoid"]].copy()
    valid_geoids = set(tracts_gdf["geoid"].dropna().astype(str))
    if len(unmatched) > 0:
        unmatched["geoid"] = unmatched["census_tract_geoid"].apply(
            lambda g: str(g) if pd.notna(g) and str(g) in valid_geoids else None
        )
        unmatched = unmatched.dropna(subset=["geoid"])
        unmatched["join_method"] = "census_field"
        links = pd.concat([links, unmatched[["location_id", "geoid", "join_method"]]])

    conn.register("loc_tract_links", links)
    conn.execute("""
        INSERT OR IGNORE INTO link_location_tract (location_id, geoid, join_method)
        SELECT location_id, geoid, join_method FROM loc_tract_links
    """)

    n = conn.execute("SELECT COUNT(*) FROM link_location_tract").fetchone()[0]
    spatial_n = len(joined[joined["geoid"].notna()])
    pct = spatial_n / len(locs_gdf) * 100
    logger.success(f"link_location_tract: {n:,} links ({pct:.1f}% of locations matched)")


def build_tract_demographics_links(conn: duckdb.DuckDBPyConnection):
    """Link each census tract to its ACS demographics row."""
    logger.info("Building link_tract_demographics...")
    conn.execute(f"""
        INSERT OR IGNORE INTO link_tract_demographics (geoid, demo_id, acs_year)
        SELECT t.geoid, d.demo_id, d.acs_year
        FROM obj_census_tract t
        JOIN obj_demographics d ON t.geoid = d.geoid
        WHERE d.acs_year = {config.CENSUS_YEAR}
    """)
    n = conn.execute("SELECT COUNT(*) FROM link_tract_demographics").fetchone()[0]
    logger.success(f"link_tract_demographics: {n:,} links")


def main():
    conn = duckdb.connect(str(config.DB_PATH))

    # Clear existing links (idempotent re-run)
    for table in [
        "link_incident_location", "link_incident_offense",
        "link_location_district", "link_location_tract", "link_tract_demographics"
    ]:
        conn.execute(f"DELETE FROM {table}")

    build_incident_location_links(conn)
    build_incident_offense_links(conn)
    build_location_district_links(conn)
    build_location_tract_links(conn)
    build_tract_demographics_links(conn)

    conn.close()
    logger.success("Link build complete.")


if __name__ == "__main__":
    main()
