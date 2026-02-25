"""
pipeline/01_ingest.py

Ingests raw data from:
  1. Austin Open Data (Crime Reports) via Socrata API
  2. US Census ACS 5-Year estimates via Census API

Saves raw data to data/raw/ as parquet files.
No transformation happens here — raw = raw.
"""

import os
import json
import requests
import pandas as pd
from pathlib import Path
from sodapy import Socrata
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

RAW_DIR = Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

CRIME_DATASET_ID = "fdj4-gpfu"  # Austin Crime Reports
SOCRATA_DOMAIN = "data.austintexas.gov"
APP_TOKEN = os.getenv("SOCRATA_APP_TOKEN", None)

# How many records to pull. Set to None for all ~2.5M records (slow).
# For dev/testing, use 100_000
RECORD_LIMIT = 100_000


def ingest_crime_reports():
    print(f"[1/2] Ingesting Austin crime reports from Socrata...")
    print(f"      Dataset: {CRIME_DATASET_ID} | Limit: {RECORD_LIMIT or 'ALL'}")

    client = Socrata(SOCRATA_DOMAIN, APP_TOKEN, timeout=60)

    # Pull in chunks to avoid memory issues with full dataset
    chunk_size = 50_000
    limit = RECORD_LIMIT or 9_999_999
    offset = 0
    all_records = []

    with tqdm(total=limit, unit="records") as pbar:
        while offset < limit:
            chunk_limit = min(chunk_size, limit - offset)
            results = client.get(
                CRIME_DATASET_ID,
                limit=chunk_limit,
                offset=offset,
                order="occurred_date_time DESC",
            )
            if not results:
                break
            all_records.extend(results)
            offset += len(results)
            pbar.update(len(results))
            if len(results) < chunk_limit:
                break

    df = pd.DataFrame(all_records)
    out_path = RAW_DIR / "crime_reports_raw.parquet"
    df.to_parquet(out_path, index=False)
    print(f"      Saved {len(df):,} records to {out_path}")
    return df


def ingest_census_tracts():
    """
    Pull ACS 5-year (2019) demographic estimates for Travis County, TX.
    Variables: total population, median income, % poverty, % white, % Black,
               % Hispanic, % renter-occupied, median age.
    """
    print(f"[2/2] Ingesting Census ACS 5-Year data for Travis County, TX...")

    api_key = os.getenv("CENSUS_API_KEY")
    if not api_key:
        print("      WARNING: No CENSUS_API_KEY in .env — Census pulls may be rate-limited.")

    variables = {
        "B01003_001E": "total_population",
        "B19013_001E": "median_household_income",
        "B17001_002E": "poverty_count",
        "B02001_002E": "white_alone",
        "B02001_003E": "black_alone",
        "B03003_003E": "hispanic_latino",
        "B25003_002E": "owner_occupied",
        "B25003_003E": "renter_occupied",
        "B01002_001E": "median_age",
    }

    var_str = ",".join(["NAME"] + list(variables.keys()))
    # Travis County FIPS: state=48, county=453
    url = (
        f"https://api.census.gov/data/2019/acs/acs5"
        f"?get={var_str}"
        f"&for=tract:*"
        f"&in=state:48%20county:453"
        + (f"&key={api_key}" if api_key else "")
    )

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    headers = data[0]
    rows = data[1:]
    df = pd.DataFrame(rows, columns=headers)

    # Rename to friendly names
    df = df.rename(columns=variables)

    # Create GEOID (standard 11-digit tract identifier)
    df["geoid"] = df["state"] + df["county"] + df["tract"]

    # Cast numeric columns
    numeric_cols = list(variables.values())
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Derived fields
    df["poverty_rate"] = df["poverty_count"] / df["total_population"]
    df["renter_rate"] = df["renter_occupied"] / (df["owner_occupied"] + df["renter_occupied"])
    df["pct_white"] = df["white_alone"] / df["total_population"]
    df["pct_black"] = df["black_alone"] / df["total_population"]
    df["pct_hispanic"] = df["hispanic_latino"] / df["total_population"]

    out_path = RAW_DIR / "census_tracts_raw.parquet"
    df.to_parquet(out_path, index=False)
    print(f"      Saved {len(df):,} census tracts to {out_path}")
    return df


def ingest_tract_geometries():
    """
    Pull Travis County census tract geometries from Census TIGER/Line.
    Saves as GeoJSON for use in spatial joins.
    """
    print("[2b] Fetching Travis County census tract geometries...")

    url = (
        "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_ACS2019/MapServer/8/query"
        "?where=STATE%3D48+AND+COUNTY%3D453"
        "&outFields=GEOID,NAME,ALAND,AWATER"
        "&outSR=4326"
        "&f=geojson"
    )

    resp = requests.get(url, timeout=60)
    resp.raise_for_status()

    geo_dir = Path("data/geo")
    geo_dir.mkdir(exist_ok=True)
    out_path = geo_dir / "travis_county_tracts.geojson"

    with open(out_path, "w") as f:
        json.dump(resp.json(), f)

    print(f"      Saved tract geometries to {out_path}")


if __name__ == "__main__":
    print("=" * 60)
    print("Austin Crime Ontology — Step 1: Ingest")
    print("=" * 60)
    ingest_crime_reports()
    ingest_census_tracts()
    ingest_tract_geometries()
    print("\nIngestion complete. Run pipeline/02_normalize.py next.")
