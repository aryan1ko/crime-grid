# Austin Crime Ontology

A local analytics pipeline that converts Austin PD crime records into an ontology-style data model in DuckDB, then generates trend outputs and maps.

## Overview
This project turns flat public-safety records into a connected object model so analysis can follow relationships across entities instead of scanning one denormalized table.

## Ontology Design Summary
- Core objects: `Incident`, `Location`, `OffenseType`, `District`, `CensusTract`, `Demographics`
- Core links: incident-to-location, incident-to-offense, location-to-district, location-to-tract, and tract-to-demographics
- Modeling goal: preserve source traceability while enabling cross-domain questions (crime patterns by place, offense mix by district, and demographic context by tract)
- Pipeline scope: ingest data, build objects/links, run analytics, and export CSV + HTML outputs to `data/outputs/`

## Latest Run Results (March 14, 2026)
Run command:
```bash
python run_pipeline.py --skip-census
```

Observed outputs from this run:
- 500,000 incidents ingested (`2019-2024`)
- Yearly volume: 2019 `48,202`; 2020 `98,864`; 2021 `91,617`; 2022 `88,095`; 2023 `86,954`; 2024 `86,268`
- Violent incidents: `65,354` (13.1%); property incidents: `198,648` (39.7%)
- Pre/post-COVID shifts (2019 -> 2021):
  - UCR `23G`: `+460.5%` (483 -> 2,707)
  - UCR `240`: `+200.3%` (1,464 -> 4,397)
  - UCR `23F`: `+73.7%` (5,493 -> 9,541)
- Outputs generated: `q3_hotspots.csv`, `q5_yoy_trend.csv`, `q6_covid_comparison.csv`, `map_hotspots.html`, `map_covid_trend.html`

## Implications from this run
- Trend analysis is working: offense composition and year-over-year deltas are usable.
- Spatial and tract-level conclusions are not reliable yet in this run:
  - `link_location_tract` produced `0` links.
  - Location dedup collapsed to a single canonical location (all 500,000 incidents at one hotspot), which indicates geospatial input quality issues.
- Priority fix before policy conclusions: restore reliable point coordinates and tract linkage, then rerun the pipeline.

## Quickstart
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run_pipeline.py
```

## Project Layout
- `ingestion/` data fetchers
- `ontology/` object + link builders
- `analysis/` analytical queries
- `viz/` map generation
- `sql/` schema definitions
- `run_pipeline.py` single entry point
