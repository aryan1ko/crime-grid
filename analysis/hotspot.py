"""
analysis/hotspot.py

Geospatial crime hotspot detection using DBSCAN clustering.
Outputs an interactive Folium map to data/hotspots.html.

This demonstrates the analytical layer of the ontology —
we're not just querying the database, we're traversing
object relationships to enrich the output.
"""

import os
import duckdb
import pandas as pd
import numpy as np
import folium
from folium.plugins import HeatMap, MarkerCluster
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import StandardScaler
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/austin_crime.duckdb")
OUTPUT_DIR = Path("data")


def load_incident_locations(con: duckdb.DuckDBPyConnection, year: int = None) -> pd.DataFrame:
    """
    Load incidents with their canonical location coordinates and
    census tract context. This traverses:
      Incident → Location → CensusTract
    """
    year_filter = f"AND i.year = {year}" if year else "AND i.year >= 2019"

    query = f"""
    SELECT
        i.incident_report_number,
        i.occurred_dt,
        i.is_violent,
        ot.ucr_category,
        l.canonical_lat     AS lat,
        l.canonical_lon     AS lon,
        l.normalized_address,
        ct.poverty_rate,
        ct.median_household_income,
        ct.total_population
    FROM incident i
    JOIN location l         ON i.location_id = l.location_id
    JOIN offense_type ot    ON i.offense_type_id = ot.offense_type_id
    LEFT JOIN census_tract ct ON l.tract_geoid = ct.geoid
    WHERE l.canonical_lat IS NOT NULL
      AND l.canonical_lon IS NOT NULL
      {year_filter}
    """
    return con.execute(query).df()


def run_dbscan_hotspots(df: pd.DataFrame, eps_km: float = 0.3, min_samples: int = 15):
    """
    DBSCAN clustering on incident coordinates.
    
    eps_km: cluster radius in kilometers
    min_samples: minimum incidents to form a hotspot
    
    Returns DataFrame with cluster labels added.
    """
    # Convert km to radians for haversine metric
    eps_rad = eps_km / 6371.0

    coords = df[["lat", "lon"]].values
    coords_rad = np.radians(coords)

    db = DBSCAN(
        eps=eps_rad,
        min_samples=min_samples,
        algorithm="ball_tree",
        metric="haversine"
    ).fit(coords_rad)

    df = df.copy()
    df["cluster"] = db.labels_

    n_clusters = len(set(db.labels_)) - (1 if -1 in db.labels_ else 0)
    n_noise = (db.labels_ == -1).sum()
    print(f"    DBSCAN: {n_clusters} hotspot clusters, {n_noise:,} noise points")

    return df, n_clusters


def build_cluster_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate cluster properties for map display."""
    clustered = df[df["cluster"] >= 0]

    summary = (
        clustered.groupby("cluster")
        .agg(
            incident_count=("incident_report_number", "count"),
            violent_count=("is_violent", "sum"),
            centroid_lat=("lat", "mean"),
            centroid_lon=("lon", "mean"),
            top_offense=("ucr_category", lambda x: x.value_counts().index[0]),
            avg_poverty_rate=("poverty_rate", "mean"),
            avg_median_income=("median_household_income", "mean"),
        )
        .reset_index()
    )

    summary["violent_pct"] = summary["violent_count"] / summary["incident_count"]
    summary = summary.sort_values("incident_count", ascending=False)

    return summary


def build_map(df: pd.DataFrame, cluster_summary: pd.DataFrame, year: int = None) -> folium.Map:
    """Build interactive Folium map with hotspot circles and heatmap."""

    # Austin city center
    m = folium.Map(location=[30.2672, -97.7431], zoom_start=12, tiles="CartoDB positron")

    title = f"Austin Crime Hotspots {'— ' + str(year) if year else '(2019–present)'}"
    title_html = f"""
        <h3 style="position:fixed; top:10px; left:50px; z-index:1000;
                   background:white; padding:8px; border-radius:5px;
                   font-family:Arial; border: 1px solid #ccc;">
            {title}
        </h3>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # Heatmap layer
    heat_data = df[["lat", "lon"]].dropna().values.tolist()
    HeatMap(heat_data, radius=15, blur=20, min_opacity=0.3).add_to(m)

    # Hotspot circles
    for _, row in cluster_summary.head(30).iterrows():
        color = "red" if row["violent_pct"] > 0.3 else "orange" if row["violent_pct"] > 0.1 else "blue"
        radius = max(100, min(600, row["incident_count"] * 2))

        poverty_str = f"{row['avg_poverty_rate']:.1%}" if pd.notna(row["avg_poverty_rate"]) else "N/A"
        income_str = f"${row['avg_median_income']:,.0f}" if pd.notna(row["avg_median_income"]) else "N/A"

        popup_html = f"""
        <b>Hotspot #{int(row['cluster']) + 1}</b><br>
        Incidents: {int(row['incident_count']):,}<br>
        Violent: {row['violent_count']:.0f} ({row['violent_pct']:.0%})<br>
        Top offense: {row['top_offense']}<br>
        Avg poverty rate: {poverty_str}<br>
        Avg median income: {income_str}
        """

        folium.Circle(
            location=[row["centroid_lat"], row["centroid_lon"]],
            radius=radius,
            color=color,
            fill=True,
            fill_opacity=0.3,
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=f"Hotspot: {int(row['incident_count']):,} incidents"
        ).add_to(m)

    # Legend
    legend_html = """
    <div style="position:fixed; bottom:30px; left:30px; z-index:1000;
                background:white; padding:10px; border-radius:5px;
                font-family:Arial; font-size:12px; border:1px solid #ccc;">
        <b>Hotspot Severity</b><br>
        <span style="color:red">●</span> >30% violent<br>
        <span style="color:orange">●</span> 10–30% violent<br>
        <span style="color:blue">●</span> <10% violent<br>
        Circle size = incident count
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    return m


if __name__ == "__main__":
    print("=" * 60)
    print("Austin Crime Ontology — Hotspot Analysis")
    print("=" * 60)

    con = duckdb.connect(DB_PATH, read_only=True)

    print("Loading incident-location-tract data...")
    df = load_incident_locations(con)
    print(f"  Loaded {len(df):,} incidents with coordinates")

    print("Running DBSCAN hotspot detection...")
    df_clustered, n_clusters = run_dbscan_hotspots(df, eps_km=0.25, min_samples=20)

    cluster_summary = build_cluster_summary(df_clustered)
    print(f"\nTop 10 hotspots:")
    print(cluster_summary[["cluster", "incident_count", "violent_count",
                            "top_offense", "centroid_lat", "centroid_lon"]].head(10).to_string(index=False))

    print("\nBuilding interactive map...")
    m = build_map(df_clustered, cluster_summary)

    out_path = OUTPUT_DIR / "hotspots.html"
    m.save(str(out_path))
    print(f"  Map saved to {out_path}")
    print("  Open in your browser to explore.")

    con.close()
