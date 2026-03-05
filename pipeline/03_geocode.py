"""
pipeline/03_geocode.py

Spatial enrichment: join incidents to census tracts.

This step resolves the Location → CensusTract relationship in the ontology.
Austin PD already reports a census_tract field, but it has ~15% null rate
and some errors. We use a spatial join against TIGER geometries as the
authoritative source, falling back to the APD-reported value.

Ontology decision: source of truth hierarchy for census_tract:
  1. Spatial join result (geometry-based, always correct if coords exist)
  2. APD-reported census_tract (often correct, sometimes null/stale)
  3. NULL (no tract linkage possible)
"""

import json
import pandas as pd
import geopandas as gpd
from pathlib import Path
from shapely.geometry import Point

PROCESSED_DIR = Path("data/processed")
GEO_DIR = Path("data/geo")


def load_tract_geometries() -> gpd.GeoDataFrame:
    geojson_path = GEO_DIR / "travis_county_tracts.geojson"
    if not geojson_path.exists():
        raise FileNotFoundError(
            f"Tract geometries not found at {geojson_path}. "
            "Run pipeline/01_ingest.py first."
        )
    gdf = gpd.read_file(geojson_path)
    gdf = gdf.set_crs("EPSG:4326")
    return gdf


def spatial_join_tracts(incidents: pd.DataFrame, tracts: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Spatial join: for each incident with valid coordinates, find the
    containing census tract.
    """
    print("  Running spatial join (incidents → census tracts)...")

    has_coords = (
        incidents["latitude"].notna() &
        incidents["longitude"].notna()
    )
    geo_incidents = incidents[has_coords].copy()
    no_coords = incidents[~has_coords].copy()

    print(f"    Incidents with coordinates: {len(geo_incidents):,}")
    print(f"    Incidents without coordinates: {len(no_coords):,}")

    # Build GeoDataFrame from incident coordinates
    geometry = [Point(lon, lat) for lon, lat in
                zip(geo_incidents["longitude"], geo_incidents["latitude"])]
    gdf_incidents = gpd.GeoDataFrame(geo_incidents, geometry=geometry, crs="EPSG:4326")

    # Spatial join
    joined = gpd.sjoin(
        gdf_incidents,
        tracts[["GEOID", "NAME", "geometry"]],
        how="left",
        predicate="within"
    )

    joined = joined.rename(columns={
        "GEOID": "tract_geoid_spatial",
        "NAME": "tract_name_spatial",
    })

    # Drop geometry and index columns from join
    joined = joined.drop(columns=["geometry", "index_right"], errors="ignore")

    # Reconcile: prefer spatial join, fall back to APD-reported
    joined["census_tract_final"] = joined["tract_geoid_spatial"].fillna(
        joined.get("census_tract", None)
    )
    no_coords["census_tract_final"] = no_coords.get("census_tract", None)
    no_coords["tract_geoid_spatial"] = None
    no_coords["tract_name_spatial"] = None

    result = pd.concat([joined, no_coords], ignore_index=True)

    # Stats
    coverage = result["census_tract_final"].notna().mean()
    spatial_coverage = result["tract_geoid_spatial"].notna().mean()
    print(f"    Spatial join coverage: {spatial_coverage:.1%}")
    print(f"    Total tract coverage (spatial + APD fallback): {coverage:.1%}")

    return result


def enrich_locations_with_tracts(
    locations: pd.DataFrame,
    tracts: gpd.GeoDataFrame
) -> pd.DataFrame:
    """
    For the Location object type, also resolve which tract each canonical
    location falls within. This is the Location → CensusTract link.
    """
    print("  Enriching Location objects with census tract membership...")

    has_coords = locations["canonical_lat"].notna() & locations["canonical_lon"].notna()
    geo_locs = locations[has_coords].copy()

    geometry = [Point(lon, lat) for lon, lat in
                zip(geo_locs["canonical_lon"], geo_locs["canonical_lat"])]
    gdf_locs = gpd.GeoDataFrame(geo_locs, geometry=geometry, crs="EPSG:4326")

    joined = gpd.sjoin(
        gdf_locs,
        tracts[["GEOID", "geometry"]],
        how="left",
        predicate="within"
    )
    joined = joined.rename(columns={"GEOID": "tract_geoid"})
    joined = joined.drop(columns=["geometry", "index_right"], errors="ignore")

    # Merge back
    locations_enriched = locations.merge(
        joined[["location_id", "tract_geoid"]],
        on="location_id",
        how="left"
    )

    print(f"    Locations with tract assignment: {locations_enriched['tract_geoid'].notna().mean():.1%}")
    return locations_enriched


if __name__ == "__main__":
    print("=" * 60)
    print("Austin Crime Ontology — Step 3: Geocode + Spatial Join")
    print("=" * 60)

    print("Loading processed data...")
    incidents = pd.read_parquet(PROCESSED_DIR / "incidents.parquet")
    locations = pd.read_parquet(PROCESSED_DIR / "locations.parquet")

    print("Loading tract geometries...")
    tracts = load_tract_geometries()
    print(f"  Loaded {len(tracts)} census tracts")

    incidents_enriched = spatial_join_tracts(incidents, tracts)
    locations_enriched = enrich_locations_with_tracts(locations, tracts)

    incidents_enriched.to_parquet(PROCESSED_DIR / "incidents_enriched.parquet", index=False)
    locations_enriched.to_parquet(PROCESSED_DIR / "locations_enriched.parquet", index=False)

    print("\nGeocoding complete. Run pipeline/04_load.py next.")
