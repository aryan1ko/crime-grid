"""
ingestion/fetch_census.py

Fetches ACS 5-Year demographic estimates at the census tract level
for Travis County, TX from the Census Bureau API.

Outputs:
    data/raw/census_demographics.parquet
    data/raw/census_tract_shapes.geojson
"""

import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

import pandas as pd
import geopandas as gpd
import requests
from loguru import logger
import config


def fetch_acs_demographics() -> pd.DataFrame:
    """Pull ACS 5-year estimates for Travis County tracts."""
    if not config.CENSUS_API_KEY:
        raise ValueError(
            "CENSUS_API_KEY not set. Get a free key at: https://api.census.gov/data/key_signup.html"
        )

    variables = list(config.ACS_VARIABLES.keys())
    var_string = ",".join(["NAME"] + variables)

    url = (
        f"https://api.census.gov/data/{config.CENSUS_YEAR}/acs/acs5"
        f"?get={var_string}"
        f"&for=tract:*"
        f"&in=state:{config.CENSUS_STATE}+county:{config.CENSUS_COUNTY}"
        f"&key={config.CENSUS_API_KEY}"
    )

    logger.info(f"Fetching ACS {config.CENSUS_YEAR} data for Travis County tracts...")
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    data = response.json()
    headers = data[0]
    rows = data[1:]

    df = pd.DataFrame(rows, columns=headers)

    # Rename to human-readable
    df = df.rename(columns=config.ACS_VARIABLES)

    # Create GEOID (standard 11-digit tract ID)
    df["geoid"] = df["state"] + df["county"] + df["tract"]
    df["census_tract_float"] = pd.to_numeric(df["tract"], errors="coerce") / 100

    # Numeric coercion (Census uses -666666666 for missing)
    for col in config.ACS_VARIABLES.values():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df.loc[df[col] < 0, col] = None

    # Derived columns
    df["poverty_rate"] = df["population_below_poverty"] / df["total_population"]
    df["unemployment_rate"] = df["unemployed"] / df["total_population"]
    df["pct_white"] = df["population_white"] / df["total_population"]
    df["pct_black"] = df["population_black"] / df["total_population"]
    df["pct_hispanic"] = df["population_hispanic"] / df["total_population"]
    df["pct_asian"] = df["population_asian"] / df["total_population"]

    logger.info(f"Fetched demographics for {len(df)} census tracts")
    return df


def fetch_tract_shapes() -> gpd.GeoDataFrame:
    """
    Download Travis County census tract shapefiles from Census TIGER.
    Returns a GeoDataFrame with tract geometry.
    """
    year = config.CENSUS_YEAR
    state = config.CENSUS_STATE
    county = config.CENSUS_COUNTY

    # TIGER/Line shapefile URL
    url = (
        f"https://www2.census.gov/geo/tiger/TIGER{year}/TRACT/"
        f"tl_{year}_{state}_tract.zip"
    )

    logger.info(f"Downloading census tract shapefiles for TX ({year})...")
    gdf = gpd.read_file(url)

    # Filter to Travis County
    gdf = gdf[gdf["COUNTYFP"] == county].copy()
    gdf = gdf.to_crs(epsg=4326)  # WGS84

    gdf = gdf.rename(columns={
        "GEOID": "geoid",
        "TRACTCE": "tract",
        "NAME": "tract_name",
        "ALAND": "area_land_sqm",
        "AWATER": "area_water_sqm",
    })

    keep_cols = ["geoid", "tract", "tract_name", "area_land_sqm", "area_water_sqm", "geometry"]
    gdf = gdf[[c for c in keep_cols if c in gdf.columns]]

    logger.info(f"Downloaded {len(gdf)} tract geometries for Travis County")
    return gdf


def main():
    demo_path = config.RAW_DIR / "census_demographics.parquet"
    shapes_path = config.RAW_DIR / "census_tract_shapes.geojson"

    if not demo_path.exists():
        df = fetch_acs_demographics()
        df.to_parquet(demo_path, index=False)
        logger.success(f"Saved demographics → {demo_path}")
    else:
        logger.info(f"Already exists: {demo_path}")

    if not shapes_path.exists():
        gdf = fetch_tract_shapes()
        gdf.to_file(shapes_path, driver="GeoJSON")
        logger.success(f"Saved tract shapes → {shapes_path}")
    else:
        logger.info(f"Already exists: {shapes_path}")


if __name__ == "__main__":
    main()
