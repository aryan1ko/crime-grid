# Austin Crime Ontology

A geospatial crime intelligence project built on Austin's public crime report data, modeled using Palantir Foundry-style ontology principles.

## What This Project Does

This project ingests Austin PD crime reports (2003–present) from the City of Austin Open Data Portal, enriches them with Census demographic data, and builds a unified **object model** (ontology) that lets you answer cross-dataset questions that neither source can answer alone.

It is structured to mirror how Palantir Foundry works: explicit object types, link types, properties with provenance, and an analytical layer on top.

---

## Object Model (Ontology)

```
Incident ──── occurred_at ────► Location
    │                               │
    └── classified_as ──► OffenseType    └── within ──► CensusTract
    │                                                        │
    └── responded_by ──► District              enriched_by ──► Demographics
```

### Object Types
| Type | Description |
|---|---|
| `Incident` | A single reported crime event |
| `Location` | A normalized address / coordinate pair |
| `OffenseType` | UCR/NIBRS offense classification |
| `District` | APD patrol district |
| `CensusTract` | Census geographic unit |
| `Demographics` | ACS demographic snapshot per tract |

### Link Types
| Link | From | To |
|---|---|---|
| `occurred_at` | Incident | Location |
| `classified_as` | Incident | OffenseType |
| `within` | Location | CensusTract |
| `enriched_by` | CensusTract | Demographics |
| `patrolled_by` | Location | District |

---

## Stack

- **Python 3.10+** — ingestion, transformation, entity resolution
- **DuckDB** — local analytical database (no server needed)
- **PostGIS / SQLite + Spatialite** — geospatial joins (census tract assignment)
- **Pandas / GeoPandas** — data wrangling
- **Folium / Kepler.gl** — visualization
- **Census API** — demographic enrichment

---

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/austin-crime-ontology
cd austin-crime-ontology

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Add your Census API key (free at api.census.gov/data/key_signup.html)

# 5. Run full pipeline
python run_pipeline.py
```

---

## Pipeline Stages

```
1. ingestion/fetch_crime.py        → Download crime reports from Socrata API
2. ingestion/fetch_census.py       → Download ACS demographics per tract
3. ingestion/fetch_boundaries.py   → Download APD districts + census tract shapefiles
4. ontology/build_objects.py       → Build normalized object tables
5. ontology/entity_resolution.py   → Deduplicate locations, resolve addresses
6. ontology/link_builder.py        → Build all link type tables
7. analysis/queries.py             → Run cross-object analytical queries
8. viz/map_builder.py              → Generate interactive maps
```

---

## Analytical Questions This Ontology Can Answer

1. Which census tracts have the highest incident density relative to population?
2. How does offense mix vary by APD district over time?
3. Which locations are repeat incident sites (hot spots)?
4. Is there a correlation between median income (ACS) and violent crime rate by tract?
5. How did crime patterns shift pre/post COVID (2019 vs 2021)?

These questions span at least 3 object types each — that's the point of the ontology.

---

## Data Sources

| Source | URL | License |
|---|---|---|
| Austin Crime Reports | https://data.austintexas.gov/Public-Safety/Crime-Reports/fdj4-gpfu | Public Domain |
| ACS 5-Year Estimates | https://api.census.gov | Public Domain |
| APD District Boundaries | https://data.austintexas.gov | Public Domain |
| Census Tract Shapefiles | https://www.census.gov/geographies/mapping-files.html | Public Domain |

---

## Ontology Design Decisions

See docs/ONTOLOGY_DESIGN.md for the full design document explaining modeling choices, tradeoffs, and known limitations.

---

## Project Structure

```
austin-crime-ontology/
├── ingestion/          # Data fetching scripts (one per source)
├── ontology/           # Object building, entity resolution, link building
├── analysis/           # Analytical queries and outputs
├── viz/                # Map and chart generation
├── sql/                # DuckDB schema definitions
├── notebooks/          # Exploratory analysis
├── docs/               # Design documents
├── run_pipeline.py     # Single entry point to run everything
├── config.py           # Shared configuration
└── requirements.txt
```
