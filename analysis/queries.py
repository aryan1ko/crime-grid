"""
analysis/queries.py

Cross-object analytical queries that demonstrate the value of the ontology.
Each query spans multiple object types and would not be possible from
a single source dataset.

Run standalone to print results, or import functions for use in notebooks.
"""

import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

import pandas as pd
import duckdb
from loguru import logger
import config


def get_conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(config.DB_PATH), read_only=True)


# ─────────────────────────────────────────────────────────────────────────────
# Q1: Which census tracts have the highest crime density per capita?
#     Spans: Incident + Location + CensusTract + Demographics
# ─────────────────────────────────────────────────────────────────────────────
def q1_crime_density_by_tract() -> pd.DataFrame:
    conn = get_conn()
    result = conn.execute("""
        SELECT
            tract_name,
            total_incidents,
            violent_incidents,
            total_population,
            median_household_income,
            ROUND(poverty_rate * 100, 1) AS poverty_rate_pct,
            ROUND(incidents_per_1k_pop, 1) AS incidents_per_1k_pop
        FROM vw_tract_crime_summary
        WHERE total_population > 100
        ORDER BY incidents_per_1k_pop DESC NULLS LAST
        LIMIT 20
    """).df()
    conn.close()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Q2: How does offense mix vary by APD district?
#     Spans: Incident + OffenseType + District
# ─────────────────────────────────────────────────────────────────────────────
def q2_offense_mix_by_district() -> pd.DataFrame:
    conn = get_conn()
    result = conn.execute("""
        SELECT
            d.district_name,
            o.ucr_category,
            COUNT(*) AS incident_count,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY d.district_name), 1) AS pct_of_district
        FROM obj_incident i
        JOIN obj_district d ON i.district_id = d.district_id
        JOIN obj_offense_type o ON i.offense_type_id = o.offense_type_id
        WHERE d.district_name IS NOT NULL
          AND o.ucr_category IS NOT NULL
        GROUP BY d.district_name, o.ucr_category
        ORDER BY d.district_name, incident_count DESC
    """).df()
    conn.close()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Q3: Top repeat-incident hotspot locations
#     Spans: Incident + Location
# ─────────────────────────────────────────────────────────────────────────────
def q3_hotspot_locations(top_n: int = 25) -> pd.DataFrame:
    conn = get_conn()
    result = conn.execute(f"""
        SELECT
            normalized_address,
            latitude,
            longitude,
            incident_count,
            zip_code,
            CAST(first_seen AS DATE) AS first_seen,
            CAST(last_seen AS DATE) AS last_seen,
            DATEDIFF('day', first_seen, last_seen) AS active_days
        FROM obj_location
        WHERE is_hotspot = TRUE
        ORDER BY incident_count DESC
        LIMIT {top_n}
    """).df()
    conn.close()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Q4: Income vs violent crime rate correlation by tract
#     Spans: Incident + Location + CensusTract + Demographics
# ─────────────────────────────────────────────────────────────────────────────
def q4_income_vs_violent_crime() -> pd.DataFrame:
    conn = get_conn()
    result = conn.execute("""
        SELECT
            tract_name,
            violent_incidents,
            total_population,
            median_household_income,
            poverty_rate,
            CASE
                WHEN total_population > 0
                THEN ROUND(violent_incidents * 1000.0 / total_population, 2)
                ELSE NULL
            END AS violent_per_1k_pop
        FROM vw_tract_crime_summary
        WHERE total_population > 200
          AND median_household_income IS NOT NULL
        ORDER BY violent_per_1k_pop DESC NULLS LAST
    """).df()
    conn.close()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Q5: Year-over-year trend by offense category
#     Spans: Incident + OffenseType
# ─────────────────────────────────────────────────────────────────────────────
def q5_yoy_offense_trend() -> pd.DataFrame:
    conn = get_conn()
    result = conn.execute("""
        SELECT
            i.year,
            o.ucr_category,
            COUNT(*) AS incident_count,
            SUM(COUNT(*)) OVER (PARTITION BY i.year) AS total_year_count,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY i.year), 2) AS pct_of_year
        FROM obj_incident i
        JOIN obj_offense_type o ON i.offense_type_id = o.offense_type_id
        WHERE i.year BETWEEN {min_year} AND {max_year}
          AND o.ucr_category IS NOT NULL
        GROUP BY i.year, o.ucr_category
        ORDER BY i.year, incident_count DESC
    """.format(min_year=config.MIN_YEAR, max_year=config.MAX_YEAR)).df()
    conn.close()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Q6: Pre/post COVID comparison (2019 vs 2021)
#     Spans: Incident + OffenseType + District
# ─────────────────────────────────────────────────────────────────────────────
def q6_pre_post_covid() -> pd.DataFrame:
    conn = get_conn()
    result = conn.execute("""
        WITH yr AS (
            SELECT
                i.year,
                o.ucr_category,
                COUNT(*) AS n
            FROM obj_incident i
            JOIN obj_offense_type o ON i.offense_type_id = o.offense_type_id
            WHERE i.year IN (2019, 2021)
            GROUP BY i.year, o.ucr_category
        ),
        pivoted AS (
            SELECT
                ucr_category,
                MAX(CASE WHEN year = 2019 THEN n ELSE 0 END) AS n_2019,
                MAX(CASE WHEN year = 2021 THEN n ELSE 0 END) AS n_2021
            FROM yr
            GROUP BY ucr_category
        )
        SELECT
            ucr_category,
            n_2019,
            n_2021,
            n_2021 - n_2019 AS absolute_change,
            CASE WHEN n_2019 > 0
                 THEN ROUND((n_2021 - n_2019) * 100.0 / n_2019, 1)
                 ELSE NULL END AS pct_change
        FROM pivoted
        ORDER BY ABS(pct_change) DESC NULLS LAST
    """).df()
    conn.close()
    return result


def print_all():
    """Print summaries of all analytical queries."""
    print("\n" + "="*60)
    print("Q1: Crime Density by Census Tract (top 20)")
    print("="*60)
    print(q1_crime_density_by_tract().to_string(index=False))

    print("\n" + "="*60)
    print("Q3: Top Hotspot Locations")
    print("="*60)
    print(q3_hotspot_locations().to_string(index=False))

    print("\n" + "="*60)
    print("Q5: Year-over-Year Offense Trend")
    print("="*60)
    print(q5_yoy_offense_trend().head(30).to_string(index=False))

    print("\n" + "="*60)
    print("Q6: Pre/Post COVID Comparison (2019 vs 2021)")
    print("="*60)
    print(q6_pre_post_covid().to_string(index=False))

    # Export to CSV
    out = config.OUTPUT_DIR
    q1_crime_density_by_tract().to_csv(out / "q1_crime_density.csv", index=False)
    q3_hotspot_locations().to_csv(out / "q3_hotspots.csv", index=False)
    q4_income_vs_violent_crime().to_csv(out / "q4_income_vs_crime.csv", index=False)
    q5_yoy_offense_trend().to_csv(out / "q5_yoy_trend.csv", index=False)
    q6_pre_post_covid().to_csv(out / "q6_covid_comparison.csv", index=False)
    logger.success(f"Query results saved → {out}")


if __name__ == "__main__":
    print_all()
