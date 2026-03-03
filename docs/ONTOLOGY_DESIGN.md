# Ontology Design Document
## Austin Crime Intelligence — Object Model

**Author:** [Your Name]  
**Dataset:** Austin PD Crime Reports + ACS Demographics  
**Purpose:** Palantir Foundry-style ontology demonstrating cross-source entity modeling

---

## 1. Overview

This document explains the modeling decisions made when constructing the Austin Crime Ontology. It is written to mirror the kind of internal design documentation Palantir ontologists produce before building in Foundry.

The core question driving every decision: *what is the right level of abstraction for an analyst to reason over this data?*

---

## 2. Object Types and Why We Chose Them

### 2.1 `Incident`
**What it represents:** A single reported crime event with a unique incident report number.

**Key decisions:**
- We use `incident_report_number` as the primary key rather than generating a surrogate. This preserves traceability back to the source record and makes deduplication auditable.
- `Incident` does *not* store address or offense text directly — those are normalized into `Location` and `OffenseType` objects and linked. This is intentional: it allows an analyst to traverse "all incidents at this location" or "all incidents of this type" without scanning the entire incident table.

**Known limitation:** Austin PD occasionally updates a report after initial filing (e.g., reclassifying a death from unknown to homicide). The current model takes a snapshot; a production ontology would need a versioning/audit trail.

---

### 2.2 `Location`
**What it represents:** A canonical physical address/coordinate pair.

**Key decisions:**
- Raw APD addresses are inconsistent (e.g., "E 7TH ST" vs "E SEVENTH STREET" for the same block). We normalize via `entity_resolution.py` using a 25-meter spatial deduplication threshold.
- We chose 25m as the threshold because: (a) GPS precision of street-level geocoding is typically ±10–20m, and (b) a block face in Austin is ~90m, so 25m avoids merging across block boundaries.
- We **do not** geocode to exact parcel level because APD intentionally obfuscates exact addresses by rounding to block centroids for privacy. Maintaining this obfuscation is the right choice.

**Tradeoff:** Aggressive deduplication risks merging truly distinct nearby locations (e.g., a convenience store and a bus stop across the street). We log all merges in `data/processed/dedup_report.csv` for manual review.

---

### 2.3 `OffenseType`
**What it represents:** A normalized crime classification based on UCR/NIBRS categories.

**Key decisions:**
- Raw APD offense descriptions (e.g., "THEFT/SHOPLIFTING") are too granular for trend analysis, but UCR categories are sometimes too coarse. We preserve both in the object and let the analyst choose.
- `is_violent` and `is_property` are boolean flags derived from UCR definitions, not APD's own classification. This is intentional — it allows consistent comparison across datasets if the ontology is later extended to include other agencies.

---

### 2.4 `District`
**What it represents:** An APD patrol district (the operational unit for law enforcement response).

**Key decisions:**
- District is modeled as a first-class object (not just a property on Incident) because analysts frequently want to *start* from a district and traverse to incidents, not the reverse.
- We enrich District with geometry from the boundary shapefile, stored as WKT. This enables future spatial queries (e.g., "incidents within 500m of district boundary").

---

### 2.5 `CensusTract` + `Demographics`
**Why are these two separate objects?**

Census tracts are geographic entities that persist across years. Demographic data is a *snapshot* — it changes year over year. Separating them means:
1. We can attach multiple demographic snapshots (2019 ACS, 2021 ACS, 2023 ACS) to the same tract object.
2. Analysts can query geographic patterns independent of demographic context, or join when needed.

This is a direct analog of how Foundry separates stable entities from time-varying properties.

---

## 3. Link Types and Design Choices

### `Location → within → CensusTract`
This is the most important link because it bridges law enforcement data (APD) with demographic data (Census). Two join methods are used:

1. **Spatial (point-in-polygon):** GeoPandas spatial join using 2021 TIGER tract geometries. This is authoritative but slow.
2. **Census field fallback:** APD data includes a `census_tract` field, but it is not always populated and may reflect outdated boundaries. Used only when spatial join fails.

The `join_method` field on the link records which method was used, enabling quality auditing.

**Known gap:** ~3–8% of locations fall outside tract boundaries (rivers, highways, boundary edge cases). These are left unlinked rather than assigned to the nearest tract, to avoid introducing false precision.

---

## 4. What This Ontology Cannot Answer (Honestly)

1. **Individual-level data:** We have incidents, not people. We cannot track recidivism or victim patterns.
2. **Real-time data:** The pipeline is a batch process. Hot spots reflect historical patterns, not current activity.
3. **Causality:** The income-vs-crime correlation query (Q4) is descriptive, not causal. Poverty does not cause crime; both are correlated with confounders not in this dataset.
4. **Unreported crime:** This is a major limitation of any police report dataset. The ontology reflects *reported* incidents, which are known to under-represent certain crime types and neighborhoods due to structural reporting biases.

These limitations should be stated clearly in any presentation of findings.

---

## 5. Extension Paths

If extending this ontology in Palantir Foundry, the highest-value additions would be:

- **311 Service Requests** (Austin Open Data) → adds a `ServiceRequest` object type linked to Location, enabling correlation between code enforcement complaints and crime
- **Business licenses** (Austin Open Data) → enables analysis of commercial activity near hotspots
- **School locations** (TEA data) → adds context for daytime juvenile incidents
- **Eviction filings** (Texas Courts data) → links housing instability to location-level crime patterns over time
