"""
analysis/temporal.py

Time-series offense pattern analysis.

Demonstrates how the ontology enables temporal queries that span
offense categories, demographics, and time — without requiring the
analyst to know about the underlying joins.
"""

import os
import duckdb
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/austin_crime.duckdb")
OUTPUT_DIR = Path("data")
sns.set_style("whitegrid")
plt.rcParams["font.family"] = "sans-serif"


def plot_annual_trends(con: duckdb.DuckDBPyConnection):
    """Annual violent vs. property crime trends, 2010–2023."""

    df = con.execute("""
        SELECT
            i.year,
            SUM(i.is_violent::int)          AS violent,
            SUM((NOT i.is_violent)::int)    AS property,
            COUNT(*)                         AS total
        FROM incident i
        WHERE i.year BETWEEN 2010 AND 2023
          AND i.year IS NOT NULL
        GROUP BY i.year
        ORDER BY i.year
    """).df()

    if df.empty:
        print("  No data for annual trends — skipping.")
        return

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(df["year"], df["violent"], marker="o", label="Violent", color="#d62728", linewidth=2)
    ax.plot(df["year"], df["property"], marker="s", label="Property", color="#1f77b4", linewidth=2)
    ax.axvline(2020, color="gray", linestyle="--", alpha=0.6, label="COVID-19 (2020)")
    ax.set_title("Austin Crime Incidents by Year (2010–2023)", fontsize=14, fontweight="bold")
    ax.set_xlabel("Year")
    ax.set_ylabel("Incident Count")
    ax.legend()
    ax.xaxis.set_major_locator(plt.MultipleLocator(1))
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "annual_trends.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  Saved: data/annual_trends.png")


def plot_hour_heatmap(con: duckdb.DuckDBPyConnection):
    """Heatmap: incident count by hour × day of week."""

    df = con.execute("""
        SELECT
            day_of_week,
            hour_of_day,
            COUNT(*) AS incidents
        FROM incident
        WHERE year >= 2019
          AND day_of_week IS NOT NULL
          AND hour_of_day IS NOT NULL
        GROUP BY day_of_week, hour_of_day
    """).df()

    if df.empty:
        print("  No data for heatmap — skipping.")
        return

    pivot = df.pivot(index="day_of_week", columns="hour_of_day", values="incidents").fillna(0)
    pivot.index = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    fig, ax = plt.subplots(figsize=(16, 5))
    sns.heatmap(pivot, cmap="YlOrRd", ax=ax, linewidths=0.3,
                cbar_kws={"label": "Incident Count"})
    ax.set_title("Crime Incidents by Day of Week × Hour (2019–present)", fontsize=13, fontweight="bold")
    ax.set_xlabel("Hour of Day (0–23)")
    ax.set_ylabel("")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "hour_heatmap.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  Saved: data/hour_heatmap.png")


def plot_offense_mix_by_poverty_quartile(con: duckdb.DuckDBPyConnection):
    """Stacked bar: offense type mix by poverty quartile."""

    df = con.execute("""
        WITH quartiles AS (
            SELECT geoid, NTILE(4) OVER (ORDER BY poverty_rate) AS q
            FROM census_tract WHERE total_population > 200
        )
        SELECT
            q.q                     AS poverty_quartile,
            ot.ucr_category,
            COUNT(*)                AS incidents
        FROM incident i
        JOIN offense_type ot ON i.offense_type_id = ot.offense_type_id
        JOIN quartiles q     ON i.tract_geoid = q.geoid
        WHERE i.year >= 2019
          AND ot.ucr_category != 'Other/Unknown'
        GROUP BY q.q, ot.ucr_category
    """).df()

    if df.empty:
        print("  No data for offense mix — skipping.")
        return

    pivot = df.pivot(index="poverty_quartile", columns="ucr_category", values="incidents").fillna(0)
    pivot = pivot.div(pivot.sum(axis=1), axis=0)  # normalize to pct

    fig, ax = plt.subplots(figsize=(10, 6))
    pivot.plot(kind="bar", stacked=True, ax=ax, colormap="tab20")
    ax.set_title("Offense Type Mix by Poverty Quartile\n(Q1=lowest poverty, Q4=highest)",
                 fontsize=13, fontweight="bold")
    ax.set_xlabel("Poverty Quartile")
    ax.set_ylabel("Share of Incidents")
    ax.set_xticklabels(["Q1 (Low)", "Q2", "Q3", "Q4 (High)"], rotation=0)
    ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize=8)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "offense_by_poverty.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  Saved: data/offense_by_poverty.png")


def plot_monthly_seasonality(con: duckdb.DuckDBPyConnection):
    """Average incidents per month, violent vs non-violent."""

    df = con.execute("""
        SELECT
            month,
            AVG(CASE WHEN is_violent THEN cnt ELSE 0 END)       AS avg_violent,
            AVG(CASE WHEN NOT is_violent THEN cnt ELSE 0 END)    AS avg_property
        FROM (
            SELECT year, month, is_violent, COUNT(*) AS cnt
            FROM incident
            WHERE year BETWEEN 2015 AND 2023
              AND month IS NOT NULL
            GROUP BY year, month, is_violent
        )
        GROUP BY month
        ORDER BY month
    """).df()

    if df.empty:
        print("  No data for seasonality — skipping.")
        return

    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(months, df["avg_violent"], label="Violent", color="#d62728", alpha=0.8)
    ax.bar(months, df["avg_property"], bottom=df["avg_violent"],
           label="Property", color="#1f77b4", alpha=0.8)
    ax.set_title("Average Monthly Incidents (2015–2023)", fontsize=13, fontweight="bold")
    ax.set_ylabel("Average Incidents")
    ax.legend()
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "monthly_seasonality.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  Saved: data/monthly_seasonality.png")


if __name__ == "__main__":
    print("=" * 60)
    print("Austin Crime Ontology — Temporal Analysis")
    print("=" * 60)

    con = duckdb.connect(DB_PATH, read_only=True)

    print("Generating charts...")
    plot_annual_trends(con)
    plot_hour_heatmap(con)
    plot_offense_mix_by_poverty_quartile(con)
    plot_monthly_seasonality(con)

    con.close()
    print("\nAll charts saved to data/. Open .png files to view.")
