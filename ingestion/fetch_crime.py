"""
ingestion/fetch_crime.py

Fetches Austin PD crime reports from the City of Austin Socrata API.
Dataset: Crime Reports (fdj4-gpfu)
Docs: https://data.austintexas.gov/Public-Safety/Crime-Reports/fdj4-gpfu

Outputs:
    data/raw/crime_reports.parquet
"""

import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

import pandas as pd
from sodapy import Socrata
from loguru import logger
from requests.exceptions import HTTPError
import config

COLUMNS_TO_KEEP = [
    "incident_report_number",
    "crime_type",
    "description",
    "occurred_date",
    "occurred_time",
    "report_date",
    "location_type",
    "address",
    "zip_code",
    "council_district",
    "apd_sector",
    "apd_district",
    "census_tract",
    "census_tract_geoid",
    "census_block_group",
    "clearance_status",
    "clearance_date",
    "ucr_category",
    "category_description",
    "x_coordinate",
    "y_coordinate",
    "latitude",
    "longitude",
]


def _extract_tract_geoid(value):
    """Extract an 11-digit tract geoid from a census block group value."""
    if pd.isna(value):
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) >= 11:
        return digits[:11]
    return None


def _normalize_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Map live Socrata schema variants to the pipeline's expected columns."""
    df = df.copy()

    # Current API fields (occ_date_time / rep_date_time / district / sector) vs legacy names.
    if "occurred_date" not in df.columns:
        if "occ_date_time" in df.columns:
            df["occurred_date"] = df["occ_date_time"]
        elif "occ_date" in df.columns:
            df["occurred_date"] = df["occ_date"]

    if "occurred_time" not in df.columns and "occ_time" in df.columns:
        df["occurred_time"] = df["occ_time"]

    if "report_date" not in df.columns:
        if "rep_date_time" in df.columns:
            df["report_date"] = df["rep_date_time"]
        elif "rep_date" in df.columns:
            df["report_date"] = df["rep_date"]

    if "apd_district" not in df.columns and "district" in df.columns:
        df["apd_district"] = df["district"]

    if "apd_sector" not in df.columns and "sector" in df.columns:
        df["apd_sector"] = df["sector"]

    if "census_tract_geoid" not in df.columns and "census_block_group" in df.columns:
        df["census_tract_geoid"] = df["census_block_group"].apply(_extract_tract_geoid)

    # Keep expected columns present even if the source omits them.
    for optional in ["address", "zip_code", "latitude", "longitude", "x_coordinate", "y_coordinate"]:
        if optional not in df.columns:
            df[optional] = None

    return df


def fetch_crime_reports(limit: int = 500_000) -> pd.DataFrame:
    """Fetch crime reports from Socrata API, filtered to config date range."""
    logger.info(f"Connecting to Socrata: {config.SOCRATA_DOMAIN}")

    logger.info(f"Fetching records ({config.MIN_YEAR}–{config.MAX_YEAR}), limit={limit:,}")
    token_candidates = [config.SOCRATA_APP_TOKEN] if config.SOCRATA_APP_TOKEN else []
    token_candidates.append(None)
    date_field_candidates = ["occurred_date", "occ_date", "occ_date_time"]

    last_error = None
    results = None
    for token in token_candidates:
        for date_field in date_field_candidates:
            where_clause = (
                f"{date_field} >= '{config.MIN_YEAR}-01-01T00:00:00' "
                f"AND {date_field} <= '{config.MAX_YEAR}-12-31T23:59:59'"
            )
            client = Socrata(config.SOCRATA_DOMAIN, token, timeout=60)

            try:
                if token:
                    logger.info("Using configured Socrata app token")
                else:
                    logger.info("Using anonymous Socrata access (no app token)")

                results = client.get(
                    config.CRIME_DATASET_ID,
                    where=where_clause,
                    limit=limit,
                    order=f"{date_field} DESC",
                )
                if len(results) == 0 and date_field != date_field_candidates[-1]:
                    logger.warning(f"Date field '{date_field}' returned 0 rows; trying next fallback field")
                    continue
                break
            except HTTPError as exc:
                last_error = exc
                msg = str(exc).lower()
                if token and "invalid app_token" in msg:
                    logger.warning("Invalid SOCRATA_APP_TOKEN, retrying without token")
                    break
                if "no-such-column" in msg and date_field != date_field_candidates[-1]:
                    logger.warning(f"Date field '{date_field}' unavailable, trying fallback field")
                    continue
                raise

        if results is not None:
            break

    if results is None:
        raise last_error if last_error else RuntimeError("Failed to fetch crime reports")

    df = pd.DataFrame.from_records(results)
    df = _normalize_schema(df)
    if df.empty:
        for col in COLUMNS_TO_KEEP:
            if col not in df.columns:
                df[col] = pd.Series(dtype="object")
    logger.info(f"Fetched {len(df):,} raw records")

    cols = [c for c in COLUMNS_TO_KEEP if c in df.columns]
    return df[cols].copy()


def clean_crime_reports(df: pd.DataFrame) -> pd.DataFrame:
    """Type coercion, deduplication, and derived column creation."""
    logger.info("Cleaning crime reports...")

    # Parse dates
    for col in ["occurred_date", "report_date", "clearance_date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Numeric
    for col in ["latitude", "longitude", "x_coordinate", "y_coordinate", "census_tract", "zip_code"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Strip whitespace
    str_cols = df.select_dtypes("object").columns
    df[str_cols] = df[str_cols].apply(lambda s: s.str.strip())

    # Drop rows with no geolocation only when geolocation exists in this schema.
    if {"latitude", "longitude"}.issubset(df.columns):
        before = len(df)
        if df[["latitude", "longitude"]].notna().any(axis=None):
            df = df.dropna(subset=["latitude", "longitude"])
            logger.warning(f"Dropped {before - len(df):,} rows missing coordinates")
        else:
            logger.warning("Source dataset has no latitude/longitude values; keeping rows without coordinates")

    # Drop duplicates on incident number
    if "incident_report_number" in df.columns:
        df = df.drop_duplicates(subset=["incident_report_number"])

    # Derived time columns
    df["year"] = df["occurred_date"].dt.year if "occurred_date" in df.columns else None
    df["month"] = df["occurred_date"].dt.month if "occurred_date" in df.columns else None
    df["hour"] = df["occurred_date"].dt.hour if "occurred_date" in df.columns else None
    df["day_of_week"] = df["occurred_date"].dt.day_name() if "occurred_date" in df.columns else None

    # Offense category flags
    df["is_violent"] = df["crime_type"].fillna("").astype(str).str.upper().apply(
        lambda x: any(v in str(x) for v in config.VIOLENT_CRIMES)
    )
    df["is_property"] = df["crime_type"].fillna("").astype(str).str.upper().apply(
        lambda x: any(v in str(x) for v in config.PROPERTY_CRIMES)
    )

    logger.info(f"Clean dataset: {len(df):,} records")
    return df


def main():
    out_path = config.RAW_DIR / "crime_reports.parquet"

    if out_path.exists():
        logger.info(f"Already exists: {out_path} — delete to re-fetch.")
        return

    df = fetch_crime_reports()
    df = clean_crime_reports(df)
    df.to_parquet(out_path, index=False)

    logger.success(f"Saved {len(df):,} records → {out_path}")
    logger.info(f"Year range: {df['year'].min()} – {df['year'].max()}")
    logger.info(f"Unique offense types: {df['crime_type'].nunique()}")
    logger.info(f"Violent: {df['is_violent'].sum():,} | Property: {df['is_property'].sum():,}")


if __name__ == "__main__":
    main()
