"""
viz/map_builder.py

Builds interactive HTML maps using Folium.

Maps generated:
    1. Crime density choropleth by census tract
    2. Hotspot location cluster map
    3. Year-over-year change map (2019 vs 2021)
    4. District-level offense type breakdown

All maps are saved as standalone HTML files that open in any browser.
"""

import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

import pandas as pd
import geopandas as gpd
import json
import duckdb
import folium
from folium.plugins import HeatMap, MarkerCluster, Fullscreen
from loguru import logger
import config

# Austin city center
AUSTIN_LAT = 30.2672
AUSTIN_LON = -97.7431


def get_conn():
    return duckdb.connect(str(config.DB_PATH), read_only=True)


def build_choropleth_map() -> folium.Map:
    """
    Choropleth: incidents per 1,000 population by census tract.
    Color encodes crime density; popup shows demographic context.
    """
    logger.info("Building crime density choropleth...")

    conn = get_conn()
    tract_data = conn.execute("""
        SELECT
            geoid, tract_name, total_incidents, violent_incidents,
            total_population, median_household_income,
            ROUND(poverty_rate * 100, 1) AS poverty_rate_pct,
            ROUND(incidents_per_1k_pop, 1) AS incidents_per_1k
        FROM vw_tract_crime_summary
        WHERE total_population > 50
    """).df()
    conn.close()

    # Load tract geometries
    shapes_path = config.RAW_DIR / "census_tract_shapes.geojson"
    if not shapes_path.exists():
        logger.warning("No census tract shapes found — skipping choropleth")
        return None
    if tract_data.empty:
        logger.warning("No tract summary rows available — skipping choropleth")
        return None

    gdf = gpd.read_file(shapes_path)
    merged = gdf.merge(tract_data, on="geoid", how="left")

    m = folium.Map(location=[AUSTIN_LAT, AUSTIN_LON], zoom_start=11, tiles="CartoDB positron")
    Fullscreen().add_to(m)

    folium.Choropleth(
        geo_data=merged.to_json(),
        data=tract_data,
        columns=["geoid", "incidents_per_1k"],
        key_on="feature.properties.geoid",
        fill_color="YlOrRd",
        fill_opacity=0.75,
        line_opacity=0.3,
        nan_fill_color="lightgray",
        legend_name="Incidents per 1,000 Population",
        name="Crime Density",
    ).add_to(m)

    # Add popups with demographic context
    for _, row in merged.iterrows():
        if row.geometry is None:
            continue
        centroid = row.geometry.centroid
        popup_html = f"""
        <b>{row.get('tract_name', 'Unknown')}</b><br>
        Total Incidents: {row.get('total_incidents', 'N/A')}<br>
        Violent: {row.get('violent_incidents', 'N/A')}<br>
        Per 1k Pop: {row.get('incidents_per_1k', 'N/A')}<br>
        Population: {row.get('total_population', 'N/A'):,}<br>
        Median Income: ${row.get('median_household_income', 0) or 0:,.0f}<br>
        Poverty Rate: {row.get('poverty_rate_pct', 'N/A')}%
        """
        folium.Marker(
            location=[centroid.y, centroid.x],
            popup=folium.Popup(popup_html, max_width=250),
            icon=folium.Icon(icon="info-sign", prefix="glyphicon", color="blue"),
        ).add_to(m)

    folium.LayerControl().add_to(m)
    return m


def build_hotspot_map() -> folium.Map:
    """
    Cluster map of repeat-incident hotspot locations.
    Size/color encodes incident count.
    """
    logger.info("Building hotspot cluster map...")

    conn = get_conn()
    hotspots = conn.execute("""
        SELECT
            normalized_address, latitude, longitude,
            incident_count, zip_code
        FROM obj_location
        WHERE is_hotspot = TRUE
        ORDER BY incident_count DESC
        LIMIT 500
    """).df()
    conn.close()

    m = folium.Map(location=[AUSTIN_LAT, AUSTIN_LON], zoom_start=11, tiles="CartoDB dark_matter")
    Fullscreen().add_to(m)
    if hotspots.empty:
        logger.warning("No hotspot locations available — generating empty hotspot map")
        return m

    # Heatmap layer
    heat_data = hotspots[["latitude", "longitude", "incident_count"]].values.tolist()
    HeatMap(heat_data, radius=20, blur=15, max_zoom=13, name="Heatmap").add_to(m)

    # Marker cluster layer
    cluster = MarkerCluster(name="Hotspot Locations")
    for _, row in hotspots.iterrows():
        popup_html = f"""
        <b>{row['normalized_address']}</b><br>
        Incidents: <b>{int(row['incident_count'])}</b><br>
        ZIP: {row.get('zip_code', 'N/A')}
        """
        color = "red" if row["incident_count"] >= 50 else "orange" if row["incident_count"] >= 20 else "beige"
        folium.Marker(
            location=[row["latitude"], row["longitude"]],
            popup=folium.Popup(popup_html, max_width=200),
            icon=folium.Icon(color=color, icon="exclamation-sign", prefix="glyphicon"),
        ).add_to(cluster)

    cluster.add_to(m)
    folium.LayerControl().add_to(m)
    return m


def build_trend_map() -> folium.Map:
    """
    Choropleth showing % change in crime rate 2019 → 2021 by district.
    Green = decreased, Red = increased.
    """
    logger.info("Building pre/post COVID trend map...")

    conn = get_conn()
    trend = conn.execute("""
        WITH yr AS (
            SELECT
                i.district_id,
                i.year,
                COUNT(*) AS n
            FROM obj_incident i
            WHERE i.year IN (2019, 2021)
            GROUP BY i.district_id, i.year
        )
        SELECT
            district_id,
            MAX(CASE WHEN year = 2019 THEN n ELSE 0 END) AS n_2019,
            MAX(CASE WHEN year = 2021 THEN n ELSE 0 END) AS n_2021,
            CASE WHEN MAX(CASE WHEN year = 2019 THEN n ELSE 0 END) > 0
                 THEN ROUND((MAX(CASE WHEN year = 2021 THEN n ELSE 0 END) - MAX(CASE WHEN year = 2019 THEN n ELSE 0 END)) * 100.0 /
                       MAX(CASE WHEN year = 2019 THEN n ELSE 0 END), 1)
                 ELSE NULL END AS pct_change
        FROM yr
        GROUP BY district_id
    """).df()

    districts = conn.execute(
        "SELECT district_id, district_name, geometry_wkt FROM obj_district"
    ).df()
    conn.close()

    merged = districts.merge(trend, on="district_id", how="left")

    m = folium.Map(location=[AUSTIN_LAT, AUSTIN_LON], zoom_start=11, tiles="CartoDB positron")
    Fullscreen().add_to(m)

    for _, row in merged.iterrows():
        if not row.get("geometry_wkt"):
            continue
        try:
            from shapely import wkt as shapely_wkt
            geom = shapely_wkt.loads(row["geometry_wkt"])
            pct = row.get("pct_change", 0) or 0
            color = f"#{max(0, min(255, int(255 * (pct / 50 + 0.5)))):02x}{max(0, min(255, int(255 * (1 - abs(pct) / 50)))):02x}00"

            popup_html = f"""
            <b>District: {row['district_name']}</b><br>
            2019 Incidents: {int(row.get('n_2019', 0))}<br>
            2021 Incidents: {int(row.get('n_2021', 0))}<br>
            Change: {pct:+.1f}%
            """
            folium.GeoJson(
                geom.__geo_interface__,
                style_function=lambda f, c=color: {
                    "fillColor": c, "fillOpacity": 0.6,
                    "color": "white", "weight": 1
                },
                tooltip=row["district_name"],
                popup=folium.Popup(popup_html, max_width=200),
            ).add_to(m)
        except Exception as e:
            logger.warning(f"Could not render district {row['district_name']}: {e}")

    return m


def main():
    output_dir = config.OUTPUT_DIR

    choropleth = build_choropleth_map()
    if choropleth:
        out = output_dir / "map_crime_density.html"
        choropleth.save(str(out))
        logger.success(f"Saved → {out}")

    hotspot = build_hotspot_map()
    out = output_dir / "map_hotspots.html"
    hotspot.save(str(out))
    logger.success(f"Saved → {out}")

    trend = build_trend_map()
    out = output_dir / "map_covid_trend.html"
    trend.save(str(out))
    logger.success(f"Saved → {out}")

    logger.success("All maps generated. Open HTML files in your browser.")


if __name__ == "__main__":
    main()
