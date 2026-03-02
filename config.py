"""
config.py — Shared configuration for the Austin Crime Ontology project.
All pipeline scripts import from here.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Helpers ────────────────────────────────────────────────────────────────────
def _optional_env(name: str):
    """
    Return an environment variable value or None for blank/placeholder tokens.
    """
    raw = os.getenv(name)
    if raw is None:
        return None

    value = raw.strip()
    if not value:
        return None

    lower = value.lower()
    if lower in {"none", "null", "nil"}:
        return None

    # Common starter placeholders used in .env examples.
    if lower.startswith("your_") or lower.endswith("_here") or "example" in lower:
        return None

    return value

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT_DIR / "data"))
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = DATA_DIR / "outputs"

for d in [RAW_DIR, PROCESSED_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "austin_crime.duckdb"))

# ── API Keys ───────────────────────────────────────────────────────────────────
CENSUS_API_KEY = _optional_env("CENSUS_API_KEY") or ""
SOCRATA_APP_TOKEN = _optional_env("SOCRATA_APP_TOKEN")

# ── Data Sources ───────────────────────────────────────────────────────────────
CRIME_DATASET_ID = "fdj4-gpfu"
SOCRATA_DOMAIN = "data.austintexas.gov"

# APD District boundary dataset
APD_DISTRICTS_DATASET_ID = "9jeg-fsk5"
APD_DISTRICTS_ARCGIS_QUERY_URL = (
    "https://services.arcgis.com/0L95CJ0VTaxqcmED/arcgis/rest/services/"
    "BOUNDARIES_apd_districts/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)

# Census config
CENSUS_YEAR = 2021
CENSUS_STATE = "48"   # Texas FIPS
CENSUS_COUNTY = "453" # Travis County FIPS

# ACS variables to pull
ACS_VARIABLES = {
    "B01003_001E": "total_population",
    "B19013_001E": "median_household_income",
    "B17001_002E": "population_below_poverty",
    "B02001_002E": "population_white",
    "B02001_003E": "population_black",
    "B02001_005E": "population_asian",
    "B03001_003E": "population_hispanic",
    "B25077_001E": "median_home_value",
    "B08301_001E": "total_commuters",
    "B23025_005E": "unemployed",
}

# ── Ontology Config ────────────────────────────────────────────────────────────
# Offense categories for normalization
VIOLENT_CRIMES = [
    "MURDER", "RAPE", "ROBBERY", "AGG ASSAULT", "FAMILY DISTURBANCE"
]
PROPERTY_CRIMES = [
    "BURGLARY", "THEFT", "AUTO THEFT", "ARSON"
]

# Location deduplication — addresses within this distance (meters) are the same
LOCATION_DEDUP_DISTANCE_M = 25

# Hot spot threshold — locations with >= N incidents are flagged
HOTSPOT_THRESHOLD = 10

# Date range filter
MIN_YEAR = 2018
MAX_YEAR = 2024
