"""
ontology/entity_resolution.py

Handles location deduplication and address normalization.

The core challenge: Austin PD reports contain slightly inconsistent addresses
for the same physical location (e.g., "100 CONGRESS AVE" vs "100 CONGRESS AV").
This script clusters nearby locations and merges duplicates into canonical objects.

Strategy:
1. Group locations by H3 hexagon (coarse spatial bucketing)
2. Within each bucket, compute pairwise distances
3. Merge locations within LOCATION_DEDUP_DISTANCE_M meters
4. Update incident foreign keys to point to canonical location

Outputs:
    Updates obj_location and obj_incident tables in DuckDB
    Saves a dedup report to data/processed/dedup_report.csv
"""

import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import duckdb
from loguru import logger
import config

try:
    import h3
    HAS_H3 = True
except ImportError:
    HAS_H3 = False
    logger.warning("h3 not installed — falling back to grid-based dedup")


def haversine_distance(lat1, lon1, lat2, lon2) -> float:
    """Return distance in meters between two WGS84 points."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def assign_h3_bucket(lat: float, lon: float, resolution: int = 10) -> str:
    """Assign H3 hex index at given resolution (~15m at res=11, ~67m at res=10)."""
    if HAS_H3:
        return h3.geo_to_h3(lat, lon, resolution)
    else:
        # Fallback: grid bucket at ~50m precision
        grid_lat = round(lat, 3)
        grid_lon = round(lon, 3)
        return f"{grid_lat}_{grid_lon}"


def find_duplicate_locations(loc_df: pd.DataFrame) -> dict:
    """
    Returns a dict mapping duplicate location_ids to their canonical location_id.
    """
    logger.info(f"Running entity resolution on {len(loc_df):,} locations...")

    threshold = config.LOCATION_DEDUP_DISTANCE_M
    loc_df = loc_df.copy()

    # Assign spatial buckets
    loc_df["bucket"] = loc_df.apply(
        lambda r: assign_h3_bucket(r["latitude"], r["longitude"]),
        axis=1,
    )

    # Also check neighboring buckets to catch cross-boundary pairs
    # For H3, neighbors are easy; for grid fallback we expand slightly
    merge_map = {}  # duplicate_id → canonical_id

    buckets = loc_df.groupby("bucket")
    checked_pairs = set()

    for bucket_id, group in buckets:
        if len(group) < 2:
            continue

        locs = group[["location_id", "latitude", "longitude", "incident_count"]].values

        for i in range(len(locs)):
            for j in range(i + 1, len(locs)):
                id_i, lat_i, lon_i, cnt_i = locs[i]
                id_j, lat_j, lon_j, cnt_j = locs[j]

                pair = tuple(sorted([id_i, id_j]))
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)

                dist = haversine_distance(lat_i, lon_i, lat_j, lon_j)
                if dist <= threshold:
                    # Canonical = the one with more incidents
                    if cnt_i >= cnt_j:
                        merge_map[id_j] = id_i
                    else:
                        merge_map[id_i] = id_j

    # Resolve transitive chains: if A→B and B→C, resolve A→C
    def resolve(loc_id, depth=0):
        if depth > 10:
            return loc_id
        if loc_id in merge_map:
            return resolve(merge_map[loc_id], depth + 1)
        return loc_id

    resolved_map = {k: resolve(v) for k, v in merge_map.items()}

    logger.info(f"Found {len(resolved_map):,} duplicate locations to merge")
    return resolved_map


def apply_deduplication(conn: duckdb.DuckDBPyConnection, merge_map: dict):
    """Update incident foreign keys and remove merged location rows."""
    if not merge_map:
        logger.info("No duplicates to merge.")
        return

    # Build update cases
    logger.info("Updating incident location foreign keys...")
    updates = pd.DataFrame([
        {"old_id": k, "new_id": v} for k, v in merge_map.items()
    ])

    conn.register("dedup_updates", updates)
    conn.execute("""
        UPDATE obj_incident
        SET location_id = u.new_id
        FROM dedup_updates u
        WHERE obj_incident.location_id = u.old_id
    """)

    # Remove merged locations (they're now orphaned)
    old_ids = list(merge_map.keys())
    placeholders = ",".join(f"'{x}'" for x in old_ids)
    conn.execute(f"DELETE FROM obj_location WHERE location_id IN ({placeholders})")

    # Recompute incident counts on canonical locations
    conn.execute("""
        UPDATE obj_location
        SET incident_count = (
            SELECT COUNT(*) FROM obj_incident
            WHERE obj_incident.location_id = obj_location.location_id
        )
    """)

    # Recompute hotspot flag
    conn.execute(f"""
        UPDATE obj_location
        SET is_hotspot = (incident_count >= {config.HOTSPOT_THRESHOLD})
    """)

    logger.success(f"Merged {len(merge_map):,} duplicate locations")


def main():
    conn = duckdb.connect(str(config.DB_PATH))

    loc_df = conn.execute("SELECT * FROM obj_location").df()

    if len(loc_df) == 0:
        logger.warning("obj_location is empty — run build_objects.py first")
        conn.close()
        return

    merge_map = find_duplicate_locations(loc_df)

    # Save dedup report
    if merge_map:
        report = pd.DataFrame([
            {"duplicate_id": k, "canonical_id": v}
            for k, v in merge_map.items()
        ])
        report_path = config.PROCESSED_DIR / "dedup_report.csv"
        report.to_csv(report_path, index=False)
        logger.info(f"Dedup report saved → {report_path}")

    apply_deduplication(conn, merge_map)
    conn.close()

    logger.success("Entity resolution complete.")


if __name__ == "__main__":
    main()
