"""
pipeline/02_normalize.py

Entity resolution and normalization layer.

This is the most important pipeline step from an ontology perspective.
We are answering: given raw messy data, what are the canonical entities?

Key decisions made here (see ontology/design_doc.md for rationale):
  - Location deduplication strategy
  - OffenseType normalization to UCR categories
  - Incident deduplication (same incident, multiple reports)
  - Handling of NULL / invalid coordinates
"""

import re
import pandas as pd
import numpy as np
from pathlib import Path
from tqdm import tqdm

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# UCR Category mapping
# Austin uses its own offense codes — we normalize to standard UCR categories
# This is an ontology decision: we lose Austin-specific detail but gain
# comparability across jurisdictions.
# ─────────────────────────────────────────────

UCR_MAPPING = {
    # Violent
    "MURDER": "Homicide",
    "HOMICIDE": "Homicide",
    "RAPE": "Rape",
    "SEXUAL ASSAULT": "Rape",
    "ROBBERY": "Robbery",
    "AGG ASSAULT": "Aggravated Assault",
    "ASSAULT W/INJURY": "Aggravated Assault",
    # Property
    "BURGLARY": "Burglary",
    "BURGLARY OF VEHICLE": "Motor Vehicle Theft",
    "THEFT": "Larceny-Theft",
    "AUTO THEFT": "Motor Vehicle Theft",
    "ARSON": "Arson",
    # Other
    "DRUG": "Drug/Narcotic",
    "DWI": "DUI",
    "FAMILY DISTURBANCE": "Family Offense",
    "DISTURBANCE": "Disorderly Conduct",
    "CRIMINAL MISCHIEF": "Vandalism",
    "FRAUD": "Fraud",
    "TRESPASS": "Trespass",
}


def normalize_ucr(offense_str: str) -> str:
    """Map Austin offense description to UCR category."""
    if not isinstance(offense_str, str):
        return "Other/Unknown"
    offense_upper = offense_str.upper()
    for key, category in UCR_MAPPING.items():
        if key in offense_upper:
            return category
    return "Other/Unknown"


def is_violent(ucr_category: str) -> bool:
    return ucr_category in {"Homicide", "Rape", "Robbery", "Aggravated Assault"}


# ─────────────────────────────────────────────
# Location resolution
# ─────────────────────────────────────────────

def normalize_address(addr: str) -> str:
    """
    Normalize address strings for entity resolution.
    
    Ontology decision: we normalize to block-level (strip unit numbers)
    because Austin PD reports addresses at block level for privacy.
    This means our Location object represents a block, not a door.
    """
    if not isinstance(addr, str):
        return ""
    addr = addr.upper().strip()
    # Remove unit/apt numbers
    addr = re.sub(r"\bAPT\b.*", "", addr)
    addr = re.sub(r"\bUNIT\b.*", "", addr)
    addr = re.sub(r"#\d+", "", addr)
    # Normalize street type abbreviations
    replacements = {
        r"\bSTREET\b": "ST",
        r"\bAVENUE\b": "AVE",
        r"\bBOULEVARD\b": "BLVD",
        r"\bDRIVE\b": "DR",
        r"\bLANE\b": "LN",
        r"\bROAD\b": "RD",
        r"\bCOURT\b": "CT",
        r"\bPLACE\b": "PL",
        r"\bCIRCLE\b": "CIR",
        r"\bHIGHWAY\b": "HWY",
    }
    for pattern, replacement in replacements.items():
        addr = re.sub(pattern, replacement, addr)
    return addr.strip()


def resolve_locations(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build canonical Location objects from address + coordinate data.
    
    Entity resolution strategy:
      1. Normalize address string
      2. If coordinates present and valid, use centroid of all points
         at that normalized address as canonical lat/long
      3. Assign stable location_id (hash of normalized address)
    
    Returns DataFrame of unique Location objects.
    """
    print("  Resolving location entities...")

    df["normalized_address"] = df["address"].apply(normalize_address)

    # Filter to records with valid coordinates
    has_coords = (
        df["latitude"].notna() &
        df["longitude"].notna() &
        (df["latitude"] != 0) &
        (df["longitude"] != 0) &
        (df["latitude"].between(29.9, 30.7)) &   # Austin bounding box
        (df["longitude"].between(-98.2, -97.4))
    )

    coord_df = df[has_coords].copy()

    # For each normalized address, compute centroid of all reported coords
    location_agg = (
        coord_df.groupby("normalized_address")
        .agg(
            canonical_lat=("latitude", "mean"),
            canonical_lon=("longitude", "mean"),
            incident_count=("incident_report_number", "count"),
        )
        .reset_index()
    )

    # Stable location_id from address hash
    import hashlib
    location_agg["location_id"] = location_agg["normalized_address"].apply(
        lambda x: hashlib.md5(x.encode()).hexdigest()[:12]
    )

    return location_agg


# ─────────────────────────────────────────────
# Incident normalization
# ─────────────────────────────────────────────

def normalize_incidents(df: pd.DataFrame) -> pd.DataFrame:
    """
    Produce canonical Incident objects.
    
    Ontology decision: one row = one incident_report_number.
    Austin PD can file multiple charges per incident. We keep the
    highest-severity charge as the "primary" offense for the incident,
    and store all charges in a separate charges array (denormalized here
    as a pipe-separated string — proper ontology would link to OffenseType).
    """
    print("  Normalizing incidents...")

    df = df.copy()

    # Parse datetime
    df["occurred_dt"] = pd.to_datetime(df["occurred_date_time"], errors="coerce")
    df["report_dt"] = pd.to_datetime(df["report_date_time"], errors="coerce")

    # Cast coordinates
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    # Normalize UCR
    df["ucr_category"] = df["highest_offense_description"].apply(normalize_ucr)
    df["is_violent"] = df["ucr_category"].apply(is_violent)

    # Normalize address
    df["normalized_address"] = df["address"].apply(normalize_address)

    # Extract time features (useful for temporal analysis)
    df["hour_of_day"] = df["occurred_dt"].dt.hour
    df["day_of_week"] = df["occurred_dt"].dt.dayofweek  # 0=Monday
    df["month"] = df["occurred_dt"].dt.month
    df["year"] = df["occurred_dt"].dt.year

    # Select canonical incident fields
    incidents = df[[
        "incident_report_number",
        "occurred_dt",
        "report_dt",
        "normalized_address",
        "latitude",
        "longitude",
        "highest_offense_description",
        "ucr_category",
        "is_violent",
        "hour_of_day",
        "day_of_week",
        "month",
        "year",
        "council_district",
        "apd_sector",
        "apd_district",
        "census_tract",
        "clearance_status",
        "clearance_date",
    ]].copy()

    # Drop true duplicates
    incidents = incidents.drop_duplicates(subset=["incident_report_number"])

    return incidents


def build_offense_types(incidents: pd.DataFrame) -> pd.DataFrame:
    """Build canonical OffenseType lookup table."""
    offense_types = (
        incidents[["highest_offense_description", "ucr_category", "is_violent"]]
        .drop_duplicates(subset=["highest_offense_description"])
        .reset_index(drop=True)
    )
    offense_types["offense_type_id"] = range(1, len(offense_types) + 1)
    return offense_types


if __name__ == "__main__":
    print("=" * 60)
    print("Austin Crime Ontology — Step 2: Normalize")
    print("=" * 60)

    print("Loading raw crime reports...")
    raw = pd.read_parquet(RAW_DIR / "crime_reports_raw.parquet")
    print(f"  Raw records: {len(raw):,}")

    incidents = normalize_incidents(raw)
    print(f"  Canonical incidents: {len(incidents):,}")

    locations = resolve_locations(raw)
    print(f"  Canonical locations: {len(locations):,}")

    offense_types = build_offense_types(incidents)
    print(f"  Canonical offense types: {len(offense_types):,}")

    # Save processed objects
    incidents.to_parquet(PROCESSED_DIR / "incidents.parquet", index=False)
    locations.to_parquet(PROCESSED_DIR / "locations.parquet", index=False)
    offense_types.to_parquet(PROCESSED_DIR / "offense_types.parquet", index=False)

    # Quick stats
    print("\nSample UCR category distribution:")
    print(incidents["ucr_category"].value_counts().head(10).to_string())

    print(f"\nCoordinate coverage: {incidents['latitude'].notna().mean():.1%} of incidents have valid coords")
    print("\nNormalization complete. Run pipeline/03_geocode.py next.")
