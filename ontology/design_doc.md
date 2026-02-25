# Ontology Design Document: Austin Crime Intelligence

**Version:** 1.0  
**Author:** [Your Name]  
**Status:** Draft  

This document records the design decisions made in constructing the Austin Crime ontology. In a Palantir Foundry engagement, this artifact would be reviewed in an ontology design session before any data is loaded. It is more important than the code.

---

## 1. Scope and Purpose

This ontology models Austin Police Department crime reports alongside Census demographic data for the purpose of:

- Cross-object analytical queries (e.g., crime rate by demographic context)
- Geospatial pattern detection (hotspot analysis)
- Temporal trend analysis by offense category and geography

It is **not** a predictive model. It is a semantic data layer that enables analysts to ask questions that span multiple source datasets without requiring them to understand the underlying joins.

---

## 2. Object Type Decisions

### 2.1 Why `Location` is a separate object type

The simplest approach would be to embed address and coordinates as properties on `Incident`. We chose not to do this for the following reasons:

1. **Entity resolution value**: Austin PD reports ~850K distinct address strings that resolve to ~220K canonical block-level locations. Normalizing this relationship allows queries like "how many incidents have occurred at this location over time" without a string-matching GROUP BY.

2. **Tract linkage**: The `Location → within → CensusTract` relationship is computed once during the spatial join, not per-incident. This is O(locations) not O(incidents) — about 4x more efficient and avoids repeated spatial lookups.

3. **Address quality analysis**: When `Location` is an object type, we can directly analyze address data quality (e.g., what fraction of locations have high coordinate variance, indicating inconsistent reporting).

**What we lose**: Some incidents don't have addresses (reported at intersection or "unknown"). These become orphaned from the location graph. We accept this loss: ~4% of incidents lack resolvable addresses.

---

### 2.2 Why `OffenseType` is a separate object type rather than a string property

An alternative design stores `ucr_category` and `raw_description` as string columns on `Incident`. This works fine for filtering. We chose a separate `OffenseType` table because:

1. **Two-tier taxonomy**: Each Austin offense string maps to a UCR category. Storing this as a lookup table means the mapping is defined once and can be updated without reprocessing all incidents.

2. **Foundry pattern**: In Foundry engagements, controlled vocabularies are almost always modeled as object types rather than denormalized strings. It enables ontology-level filtering ("show all incidents of type X") without knowing the underlying string values.

**Design tension**: `OffenseType` has only ~80 distinct values. Some would argue this is a lookup table, not an object type. The distinction matters because object types in Foundry are first-class browsable entities. We modeled it as an object type because analysts should be able to browse to "Robbery" and see all linked incidents.

---

### 2.3 What we chose NOT to model

**Charge** (not modeled): Austin PD records multiple charges per incident. We use only the "highest offense" and discard secondary charges. A complete ontology would model `Charge` as a separate object type with a many-to-one link to `Incident`. We accepted this simplification because:
- The "highest offense" covers 95%+ of analytical use cases
- Modeling charges requires understanding APD's charge hierarchy
- Adding `Charge` would double the number of objects in the graph for marginal analytical gain at this scope

**Reporting Officer** (not modeled): The dataset includes a district/sector field but not individual officer IDs. Even if it did, officer-level analysis raises policy questions outside the scope of this project.

**Victim/Suspect** (not modeled): Intentionally excluded. APD's public dataset does not include PII, and modeling people as objects would require a privacy framework this project does not implement.

---

## 3. Link Type Decisions

### 3.1 `Incident → occurs_at → Location` vs. embedding coordinates on Incident

See Section 2.1. The short version: separate link type wins because Location is analytically useful as a first-class browsable entity.

### 3.2 `Location → within → CensusTract` source of truth hierarchy

Austin PD reports a `census_tract` field on incidents, but it has a ~15% null rate and occasional errors (tracts that don't exist in the 2019 TIGER shapefile). We implemented a source-of-truth hierarchy:

1. **Spatial join result** (authoritative): If `latitude`/`longitude` are valid and fall within a known tract polygon, use that tract.
2. **APD-reported census_tract** (fallback): Use when coordinates are null or outside Austin's bounding box.
3. **NULL**: Accept when neither source is available (~4% of incidents).

**Why this matters for the ontology**: If we trusted the APD-reported field blindly, ~15% of Location objects would have no tract assignment, and some would have incorrect assignments. The spatial join costs compute upfront but produces a much higher-quality `within` link.

---

## 4. Provenance Model

Every object table includes:
- `data_source`: the upstream dataset this object was derived from
- `load_timestamp`: when the object was written to the ontology

This is a minimal provenance model. A production Foundry ontology would track:
- Dataset version / snapshot date
- Transform job ID
- Source row hash (for change detection)

We accept the simplified model because this project uses a single ingestion run rather than incremental updates.

---

## 5. Known Limitations and Open Questions

| Issue | Impact | Decision |
|---|---|---|
| Block-level address resolution | Cannot distinguish incidents within same block | Accepted — APD design, not fixable |
| ACS 2019 demographics | ~4 years stale at load time | Accepted for v1; should refresh to ACS 2021 |
| UCR mapping is partial | ~12% of incidents map to "Other/Unknown" | Mapping should be expanded with APD's offense code legend |
| No temporal versioning of demographics | Tract demographics don't change per year | Known gap — material for multi-year analysis |
| Coordinate jitter | Some incidents report same address with slightly different lat/lon | Centroid approach absorbs this; doesn't affect analysis |

---

## 6. How This Maps to Palantir Foundry Concepts

| This Project | Foundry Equivalent |
|---|---|
| `census_tract` table | Object Type: Census Tract |
| `location` table | Object Type: Location |
| `offense_type` table | Object Type: Offense Type (controlled vocabulary) |
| `incident` table | Object Type: Incident |
| `link_incident_location` | Link Type: occurs_at |
| `link_location_tract` | Link Type: within |
| `pipeline/02_normalize.py` | Pipeline Transform (entity resolution step) |
| `pipeline/03_geocode.py` | Pipeline Transform (enrichment step) |
| `pipeline/04_load.py` | Ontology sync / writeback |
| This document | Ontology Design Review artifact |

The key difference from a real Foundry deployment: Foundry's ontology layer sits on top of datasets and is queryable via the Object Storage API. Our DuckDB schema is a structural analog, not a functional replacement.
