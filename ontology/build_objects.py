"""
ontology/build_objects.py

Transforms raw ingested data into normalized object type tables
and loads them into DuckDB. This is the core ontology construction step.

Inputs:
    data/raw/crime_reports.parquet
    data/raw/census_demographics.parquet
    data/raw/census_tract_shapes.geojson
    data/raw/apd_districts.geojson

Outputs:
    DuckDB tables: obj_location, obj_incident, obj_offense_type,
                   obj_district, obj_census_tract, obj_demographics
"""

import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

import hashlib
import re
import pandas as pd
import geopandas as gpd
import duckdb
from loguru import logger
import config


def get_db() -> duckdb.DuckDBPyConnection:
    """Open DuckDB connection and initialize schema."""
    conn = duckdb.connect(str(config.DB_PATH))
    schema_path = config.ROOT_DIR / "sql" / "schema.sql"
    conn.execute(schema_path.read_text())
    return conn


def make_location_id(address: str, lat: float, lon: float) -> str:
    """Create a stable ID for a location from its address + coords."""
    lat_val = float(lat) if pd.notna(lat) else 0.0
    lon_val = float(lon) if pd.notna(lon) else 0.0
    key = f"{str(address).lower().strip()}|{round(lat_val, 5)}|{round(lon_val, 5)}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def normalize_address(raw: str) -> str:
    """Basic address normalization."""
    if not raw or pd.isna(raw):
        return ""
    addr = str(raw).upper().strip()
    # Expand common abbreviations
    replacements = {
        r"\bST\b": "STREET", r"\bAVE\b": "AVENUE", r"\bBLVD\b": "BOULEVARD",
        r"\bDR\b": "DRIVE", r"\bLN\b": "LANE", r"\bRD\b": "ROAD",
        r"\bCT\b": "COURT", r"\bPL\b": "PLACE", r"\bPKWY\b": "PARKWAY",
        r"\bHWY\b": "HIGHWAY", r"\bN\b": "NORTH", r"\bS\b": "SOUTH",
        r"\bE\b": "EAST", r"\bW\b": "WEST",
    }
    for pattern, replacement in replacements.items():
        addr = re.sub(pattern, replacement, addr)
    return addr


def slugify(text: str) -> str:
    """Convert offense type to a clean ID string."""
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower().strip()).strip("_")


def _coerce_tract_geoid(value):
    """Normalize tract identifiers to an 11-digit GEOID when possible."""
    if pd.isna(value):
        return None

    digits = "".join(ch for ch in str(value) if ch.isdigit())
    # Current APD field is often county+tract+block-group (10 digits), no state prefix.
    if len(digits) == 10:
        return f"{config.CENSUS_STATE}{digits[:9]}"
    if len(digits) >= 11:
        return digits[:11]

    # Legacy APD field can be a tract float (e.g., 8.03 -> 000803).
    try:
        tract_num = int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None
    return f"{config.CENSUS_STATE}{config.CENSUS_COUNTY}{tract_num:06d}"


def _district_centroid_lookup(districts_gdf: gpd.GeoDataFrame) -> dict:
    """Return district name -> centroid lat/lon mapping."""
    if districts_gdf is None or len(districts_gdf) == 0:
        return {}

    name_cols = [c for c in districts_gdf.columns if "district" in c.lower() or "name" in c.lower()]
    if not name_cols:
        return {}

    name_col = name_cols[0]
    gdf = districts_gdf.to_crs(epsg=4326)
    lookup = {}
    for _, row in gdf.iterrows():
        if row.geometry is None:
            continue
        key = str(row[name_col]).strip().upper()
        centroid = row.geometry.centroid
        lookup[key] = (centroid.y, centroid.x)
    return lookup


def _tract_centroid_lookup(shapes_gdf: gpd.GeoDataFrame) -> dict:
    """Return tract geoid -> centroid lat/lon mapping."""
    if shapes_gdf is None or len(shapes_gdf) == 0:
        return {}

    geoid_col = "geoid" if "geoid" in shapes_gdf.columns else "GEOID" if "GEOID" in shapes_gdf.columns else None
    if geoid_col is None:
        return {}

    gdf = shapes_gdf.to_crs(epsg=4326)
    lookup = {}
    for _, row in gdf.iterrows():
        if row.geometry is None:
            continue
        geoid = _coerce_tract_geoid(row[geoid_col])
        if not geoid:
            continue
        centroid = row.geometry.centroid
        lookup[geoid] = (centroid.y, centroid.x)
    return lookup


def prepare_location_inputs(
    df: pd.DataFrame,
    districts_gdf: gpd.GeoDataFrame = None,
    shapes_gdf: gpd.GeoDataFrame = None,
) -> pd.DataFrame:
    """Prepare normalized address/geoid/coordinate fields for location joins."""
    work = df.copy()
    for col in ["address", "latitude", "longitude", "zip_code", "apd_district", "census_tract_geoid"]:
        if col not in work.columns:
            work[col] = None

    if "census_block_group" not in work.columns:
        work["census_block_group"] = None

    if "census_tract_geoid" not in work.columns or work["census_tract_geoid"].isna().all():
        if "census_block_group" in work.columns:
            work["census_tract_geoid"] = work["census_block_group"].apply(_coerce_tract_geoid)
        elif "census_tract" in work.columns:
            work["census_tract_geoid"] = work["census_tract"].apply(_coerce_tract_geoid)
    else:
        work["census_tract_geoid"] = work["census_tract_geoid"].apply(_coerce_tract_geoid)

    work["address"] = work["address"].fillna("").astype(str).str.strip()
    missing_address = work["address"].eq("") | work["address"].str.lower().isin({"nan", "none"})
    if missing_address.any():
        # New APD schema omits street address; use stable district/block-group surrogate.
        work.loc[missing_address, "address"] = work.loc[missing_address].apply(
            lambda r: (
                f"DISTRICT {r.get('apd_district', 'UNKNOWN')} "
                f"TRACT {r.get('census_tract_geoid', 'UNKNOWN')} "
                f"BLOCK_GROUP {r.get('census_block_group', 'UNKNOWN')}"
            ),
            axis=1,
        )

    work["latitude"] = pd.to_numeric(work["latitude"], errors="coerce")
    work["longitude"] = pd.to_numeric(work["longitude"], errors="coerce")

    missing_coords = work["latitude"].isna() | work["longitude"].isna()

    # First fallback: census tract centroid (best granularity available in current APD schema).
    tract_centroids = _tract_centroid_lookup(shapes_gdf)
    if missing_coords.any() and tract_centroids:
        centroid_points = work["census_tract_geoid"].apply(
            lambda g: tract_centroids.get(g, None) if pd.notna(g) else None
        )
        work.loc[missing_coords, "latitude"] = centroid_points[missing_coords].apply(
            lambda p: p[0] if isinstance(p, tuple) else float("nan")
        )
        work.loc[missing_coords, "longitude"] = centroid_points[missing_coords].apply(
            lambda p: p[1] if isinstance(p, tuple) else float("nan")
        )
        missing_coords = work["latitude"].isna() | work["longitude"].isna()
        logger.warning("Input crime rows lacked coordinates; backfilled from census tract centroids")

    # Second fallback: district centroid.
    if missing_coords.any():
        district_centroids = _district_centroid_lookup(districts_gdf)
        if district_centroids:
            district_key = work["apd_district"].fillna("").astype(str).str.upper()
            centroid_points = district_key.map(district_centroids)
            work.loc[missing_coords, "latitude"] = centroid_points[missing_coords].apply(
                lambda p: p[0] if isinstance(p, tuple) else float("nan")
            )
            work.loc[missing_coords, "longitude"] = centroid_points[missing_coords].apply(
                lambda p: p[1] if isinstance(p, tuple) else float("nan")
            )
            logger.warning("Backfilled remaining missing coordinates from district centroids")

    # Final fallback keeps pipeline operational if no geometry sources are available.
    work["latitude"] = work["latitude"].fillna(30.2672)
    work["longitude"] = work["longitude"].fillna(-97.7431)
    return work


def build_offense_types(df: pd.DataFrame) -> pd.DataFrame:
    """Build the OffenseType object table from crime data."""
    logger.info("Building OffenseType objects...")

    offense_df = (
        df.groupby(["crime_type", "ucr_category", "category_description"])
        .agg(
            incident_count=("incident_report_number", "count"),
            is_violent=("is_violent", "first"),
            is_property=("is_property", "first"),
        )
        .reset_index()
    )

    offense_df["offense_type_id"] = offense_df["crime_type"].apply(slugify)
    offense_df = offense_df.rename(columns={"crime_type": "raw_crime_type"})

    # Deduplicate if slug collisions
    offense_df = offense_df.drop_duplicates(subset=["offense_type_id"])

    logger.info(f"Built {len(offense_df)} OffenseType objects")
    return offense_df


def build_locations(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the Location object table.

    Key decisions:
    - One Location per unique (normalized_address, lat, lon) tuple
    - Locations within LOCATION_DEDUP_DISTANCE_M meters are candidates
      for merging (handled in entity_resolution.py)
    - Census tract and district are populated by link_builder.py
    """
    logger.info("Building Location objects...")

    work = df.copy()

    loc_df = (
        work.groupby(["address", "latitude", "longitude"])
        .agg(
            zip_code=("zip_code", "first"),
            census_tract_geoid=("census_tract_geoid", "first"),
            apd_district=("apd_district", "first"),
            incident_count=("incident_report_number", "count"),
            first_seen=("occurred_date", "min"),
            last_seen=("occurred_date", "max"),
        )
        .reset_index()
    )

    loc_df["location_id"] = loc_df.apply(
        lambda r: make_location_id(r["address"], r["latitude"], r["longitude"]),
        axis=1,
    )
    loc_df["normalized_address"] = loc_df["address"].apply(normalize_address)
    loc_df["raw_address"] = loc_df["address"]
    loc_df["is_hotspot"] = loc_df["incident_count"] >= config.HOTSPOT_THRESHOLD
    loc_df["source"] = "austin_apd"

    loc_df = loc_df.drop(columns=["address"])
    loc_df = loc_df[
        [
            "location_id",
            "raw_address",
            "normalized_address",
            "latitude",
            "longitude",
            "zip_code",
            "census_tract_geoid",
            "apd_district",
            "incident_count",
            "is_hotspot",
            "first_seen",
            "last_seen",
            "source",
        ]
    ]

    hotspots = loc_df["is_hotspot"].sum()
    logger.info(f"Built {len(loc_df):,} Location objects ({hotspots:,} hotspots)")
    return loc_df


def build_incidents(df: pd.DataFrame, loc_df: pd.DataFrame) -> pd.DataFrame:
    """Build the Incident object table, joining location_id."""
    logger.info("Building Incident objects...")

    # Join location_id back
    loc_map = loc_df.set_index(["raw_address", "latitude", "longitude"])["location_id"]

    inc_df = df.copy()
    inc_df["location_id"] = inc_df.apply(
        lambda r: loc_map.get((r["address"], r["latitude"], r["longitude"]), None),
        axis=1,
    )
    inc_df["offense_type_id"] = inc_df["crime_type"].apply(slugify)
    apd_district = inc_df["apd_district"] if "apd_district" in inc_df.columns else pd.Series([None] * len(inc_df))
    inc_df["district_id"] = apd_district.apply(
        lambda x: slugify(x) if pd.notna(x) else None
    )

    keep = [
        "incident_report_number", "location_id", "offense_type_id", "district_id",
        "occurred_date", "report_date", "year", "month", "hour", "day_of_week",
        "clearance_status", "clearance_date", "is_violent", "is_property",
    ]
    inc_df = inc_df[[c for c in keep if c in inc_df.columns]]
    inc_df = inc_df.rename(columns={
        "incident_report_number": "incident_id",
        "occurred_date": "occurred_at",
        "report_date": "reported_at",
    })
    inc_df["source"] = "austin_apd"
    inc_df = inc_df[
        [
            "incident_id",
            "location_id",
            "offense_type_id",
            "district_id",
            "occurred_at",
            "reported_at",
            "year",
            "month",
            "hour",
            "day_of_week",
            "clearance_status",
            "clearance_date",
            "is_violent",
            "is_property",
            "source",
        ]
    ]

    logger.info(f"Built {len(inc_df):,} Incident objects")
    return inc_df


def build_districts(df: pd.DataFrame, districts_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Build District object table from APD boundaries + crime data."""
    logger.info("Building District objects...")

    # Get unique districts from crime data
    crime_districts = df["apd_district"].dropna().unique()

    district_records = []
    for d in crime_districts:
        district_records.append({
            "district_id": slugify(d),
            "district_name": str(d),
            "sector": None,
            "incident_count": int((df["apd_district"] == d).sum()),
            "geometry_wkt": None,
        })

    dist_df = pd.DataFrame(district_records).drop_duplicates(subset=["district_id"])

    # Try to enrich with geometry from boundaries file
    if districts_gdf is not None and len(districts_gdf) > 0:
        # Find the district name column
        name_cols = [c for c in districts_gdf.columns if "district" in c.lower() or "name" in c.lower()]
        if name_cols:
            name_col = name_cols[0]
            for idx, row in dist_df.iterrows():
                match = districts_gdf[
                    districts_gdf[name_col].astype(str).str.upper() == row["district_name"].upper()
                ]
                if len(match) > 0:
                    dist_df.at[idx, "geometry_wkt"] = match.iloc[0].geometry.wkt

    logger.info(f"Built {len(dist_df)} District objects")
    return dist_df


def build_census_tracts(shapes_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Build CensusTract object table from shapefile."""
    logger.info("Building CensusTract objects...")

    tract_df = shapes_gdf[["geoid", "tract", "tract_name", "area_land_sqm", "area_water_sqm"]].copy()
    tract_df["geometry_wkt"] = shapes_gdf.geometry.apply(lambda g: g.wkt if g else None)

    logger.info(f"Built {len(tract_df)} CensusTract objects")
    return tract_df


def build_demographics(demo_df: pd.DataFrame) -> pd.DataFrame:
    """Build Demographics object table from ACS data."""
    logger.info("Building Demographics objects...")

    demo_df = demo_df.copy()
    demo_df["acs_year"] = config.CENSUS_YEAR
    demo_df["demo_id"] = demo_df["geoid"] + "_" + str(config.CENSUS_YEAR)
    demo_df["source"] = "acs5"

    logger.info(f"Built {len(demo_df)} Demographics objects")
    return demo_df


def load_to_duckdb(conn: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame):
    """Load a DataFrame into DuckDB, replacing existing data."""
    table_cols = [
        row[1] for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    ]
    insert_cols = [c for c in table_cols if c in df.columns]
    if not insert_cols:
        raise ValueError(f"No matching columns to load into {table}")

    conn.register("tmp_df", df[insert_cols])
    conn.execute(f"DELETE FROM {table}")
    col_list = ", ".join(insert_cols)
    conn.execute(f"INSERT INTO {table} ({col_list}) SELECT {col_list} FROM tmp_df")
    logger.success(f"Loaded {len(df):,} rows → {table}")


def main():
    conn = get_db()

    # Load raw data
    crime_path = config.RAW_DIR / "crime_reports.parquet"
    if not crime_path.exists():
        raise FileNotFoundError(f"Run ingestion/fetch_crime.py first: {crime_path}")

    logger.info("Loading raw data...")
    df = pd.read_parquet(crime_path)

    shapes_path = config.RAW_DIR / "census_tract_shapes.geojson"
    shapes_gdf = gpd.read_file(shapes_path) if shapes_path.exists() else None

    demo_path = config.RAW_DIR / "census_demographics.parquet"
    demo_df = pd.read_parquet(demo_path) if demo_path.exists() else None

    districts_path = config.RAW_DIR / "apd_districts.geojson"
    districts_gdf = gpd.read_file(districts_path) if districts_path.exists() else None

    # Build and load each object type
    prepared_df = prepare_location_inputs(df, districts_gdf=districts_gdf, shapes_gdf=shapes_gdf)

    offense_df = build_offense_types(prepared_df)
    load_to_duckdb(conn, "obj_offense_type", offense_df)

    loc_df = build_locations(prepared_df)
    load_to_duckdb(conn, "obj_location", loc_df)

    inc_df = build_incidents(prepared_df, loc_df)
    load_to_duckdb(conn, "obj_incident", inc_df)

    dist_df = build_districts(prepared_df, districts_gdf)
    load_to_duckdb(conn, "obj_district", dist_df)

    if shapes_gdf is not None:
        tract_df = build_census_tracts(shapes_gdf)
        load_to_duckdb(conn, "obj_census_tract", tract_df)

    if demo_df is not None:
        demo_obj = build_demographics(demo_df)
        load_to_duckdb(conn, "obj_demographics", demo_obj)

    conn.close()
    logger.success("Object build complete.")


if __name__ == "__main__":
    main()
