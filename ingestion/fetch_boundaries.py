"""
ingestion/fetch_boundaries.py

Fetches APD patrol district boundary shapefiles from Austin's open data portal.

Outputs:
    data/raw/apd_districts.geojson
"""

import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

import geopandas as gpd
import requests
from loguru import logger
import config


def _is_usable_gdf(gdf: gpd.GeoDataFrame) -> bool:
    """True when a GeoDataFrame has at least one non-null geometry."""
    if gdf is None or gdf.empty:
        return False
    return gdf.geometry.notna().any()


def fetch_apd_districts() -> gpd.GeoDataFrame:
    """
    Fetch APD district boundaries from Austin Open Data.
    Falls back to a GeoJSON download if Socrata GeoJSON endpoint fails.
    """
    # Try Socrata GeoJSON first (dataset id provided in config).
    url = f"https://{config.SOCRATA_DOMAIN}/resource/{config.APD_DISTRICTS_DATASET_ID}.geojson"
    params = {"$limit": 100}
    if config.SOCRATA_APP_TOKEN:
        params["$$app_token"] = config.SOCRATA_APP_TOKEN

    logger.info("Fetching APD district boundaries...")
    response = requests.get(url, params=params, timeout=30)

    if response.status_code == 200:
        data = response.json()
        features = data.get("features", [])
        if not features:
            logger.warning("Socrata GeoJSON had no features")
            gdf = None
        else:
            gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
            if not _is_usable_gdf(gdf):
                logger.warning("Socrata GeoJSON returned empty/null geometries")
                gdf = None
    else:
        logger.warning(f"Socrata endpoint failed ({response.status_code}); trying fallbacks")
        gdf = None

    if gdf is None:
        # Legacy geospatial export endpoint (kept as an intermediate fallback).
        fallback_url = (
            f"https://{config.SOCRATA_DOMAIN}/api/geospatial/{config.APD_DISTRICTS_DATASET_ID}"
            "?method=export&format=GeoJSON"
        )
        logger.warning(f"Trying geospatial export fallback: {fallback_url}")
        try:
            gdf = gpd.read_file(fallback_url)
            if not _is_usable_gdf(gdf):
                gdf = None
        except Exception as exc:
            logger.warning(f"Geospatial export fallback failed: {exc}")
            gdf = None

    if gdf is None:
        # ArcGIS service behind the federated map item.
        logger.warning(f"Trying ArcGIS fallback: {config.APD_DISTRICTS_ARCGIS_QUERY_URL}")
        gdf = gpd.read_file(config.APD_DISTRICTS_ARCGIS_QUERY_URL)
        if not _is_usable_gdf(gdf):
            raise ValueError("ArcGIS fallback returned no usable APD district geometry")

    # Normalize district name for downstream matching.
    if "DISTRICT_NAME" in gdf.columns and "district_name" not in gdf.columns:
        gdf = gdf.rename(columns={"DISTRICT_NAME": "district_name"})

    gdf = gdf.to_crs(epsg=4326)
    logger.info(f"Loaded {len(gdf)} APD district geometries")
    return gdf


def main():
    out_path = config.RAW_DIR / "apd_districts.geojson"

    if out_path.exists():
        logger.info(f"Already exists: {out_path}")
        return

    gdf = fetch_apd_districts()
    gdf.to_file(out_path, driver="GeoJSON")
    logger.success(f"Saved APD districts → {out_path}")
    logger.info(f"Columns: {list(gdf.columns)}")


if __name__ == "__main__":
    main()
