# How This Project Maps to Palantir Foundry

Use this document when explaining the project in an interview or cover letter.

---

## The Core Narrative

Most people who work with crime data build a flat table and run sklearn on it. This project does something different: it asks *what is the right object model for this domain*, then builds an analytical layer on top of that model.

That is exactly what a Palantir Ontologist does on day one of a new deployment.

---

## Foundry Concept Mapping

### Datasets → Object Types

In Foundry, raw datasets are not directly queryable by analysts. They are transformed into **Object Types** — semantic entities with defined properties, primary keys, and relationships. This project implements the same pattern:

| Raw CSV column(s) | Becomes | Object Type |
|---|---|---|
| `address`, `latitude`, `longitude` | → | `Location` (resolved, canonical) |
| `highest_offense_description` | → | `OffenseType` (normalized to UCR) |
| All incident fields | → | `Incident` |
| Census API response | → | `CensusTract` + `Demographics` |

### Transforms → Pipeline

Foundry's **Pipeline Builder** defines transforms that take raw datasets and produce object-type-aligned datasets. Our `pipeline/` directory is a direct analog:

| `pipeline/01_ingest.py` | Raw dataset ingestion (Foundry: dataset registration) |
| `pipeline/02_normalize.py` | Entity resolution transform |
| `pipeline/03_geocode.py` | Enrichment transform (spatial join) |
| `pipeline/04_load.py` | Ontology sync (Foundry: writeback transform) |

### Object Storage API → DuckDB

In Foundry, the **Object Storage API** lets you query objects and traverse links without writing joins. Our DuckDB schema with explicit link tables is a structural analog:

```python
# What an analyst does in Foundry (pseudocode):
incidents = ontology.objects.Incident
    .filter(is_violent=True, year=2023)
    .with_linked(Location)
    .with_linked(CensusTract)

# What an analyst does in our DuckDB schema:
SELECT i.*, l.*, ct.*
FROM incident i
JOIN location l      ON i.location_id = l.location_id
JOIN census_tract ct ON l.tract_geoid = ct.geoid
WHERE i.is_violent = TRUE AND i.year = 2023;
```

The difference is UX. The ontology version lets a non-technical analyst traverse the graph. Ours requires SQL. Same underlying model.

### Design Doc → Ontology Design Review

Palantir engagements begin with an **Ontology Design Review** — a meeting where the ontologist presents the proposed object model and defends each decision before implementation begins. `ontology/design_doc.md` is this artifact.

In an interview, if asked "how would you approach a new deployment," walk through that doc section by section.

---

## Talking Points for Interviews

**On entity resolution:**
> "The address field in Austin PD's data has ~850K distinct strings that resolve to ~220K canonical locations after normalization. That 4:1 ratio is the kind of data quality problem that breaks flat-table analysis but is cleanly handled by modeling Location as a separate object type with a stable primary key."

**On the ontology design tradeoff:**
> "I considered modeling Charge as a separate object type to capture APD's multi-offense incidents. I chose not to because it would double the graph size for a dataset that only exposes the 'highest offense,' giving you complexity without analytical value. I'd revisit that if the use case called for charge-level analysis."

**On the Census join:**
> "The spatial join is where the ontology's value becomes concrete. You can't ask 'how does violent crime rate correlate with poverty rate' against APD data alone. The Location → within → CensusTract link is what makes that query possible, and it's computed once at the location level rather than per-incident."

**On Foundry specifically:**
> "This project doesn't run on Foundry, but every structural decision maps to Foundry concepts. The DuckDB schema with link tables mirrors how I'd define object types and link types in the Ontology Manager. The pipeline steps map to transforms. The design doc is the artifact I'd bring to an ontology design review."

---

## What You'd Do Differently in a Real Foundry Deployment

1. Object types would be defined in the Ontology Manager UI, not in SQL
2. Transforms would be versioned and auditable via Pipeline Builder
3. The ontology would be versioned — you'd see property values *as of* a given branch
4. The Object Storage API would replace the DuckDB query layer
5. Permissions and row-level security would be configured per object type
6. This ontology would be one layer in a larger deployment — likely combined with 911 dispatch, permits, and zoning data into a shared city operations ontology
