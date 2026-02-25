"""
ontology/object_types.py

Python dataclasses representing each Object Type in the ontology.

These serve two purposes:
  1. Strongly-typed in-memory representation during pipeline processing
  2. Documentation — reading these classes tells you exactly what
     properties each object type has and where they come from

In Palantir Foundry, these would be defined in the Ontology Manager UI.
Here we express the same concepts in code.
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class CensusTract:
    """
    Object Type: CensusTract
    
    A US Census geographic unit. Properties from ACS 5-Year 2019.
    
    Palantir Foundry equivalent:
      - Object type: "Census Tract"
      - Primary key: geoid (string, 11-digit FIPS)
      - Properties: all fields below
      - Linked from: Location (via within link)
    """
    geoid: str                              # 11-digit FIPS, e.g. "48453001100"
    name: Optional[str] = None
    total_population: Optional[int] = None
    median_household_income: Optional[float] = None
    poverty_rate: Optional[float] = None    # poverty_count / total_population
    renter_rate: Optional[float] = None     # renter / (owner + renter)
    pct_white: Optional[float] = None
    pct_black: Optional[float] = None
    pct_hispanic: Optional[float] = None
    median_age: Optional[float] = None
    data_source: str = "ACS 5-Year 2019"
    load_timestamp: Optional[datetime] = None


@dataclass
class Location:
    """
    Object Type: Location
    
    A unique physical location where incidents occur. Resolution unit
    is a street block (Austin PD anonymizes to block level).
    
    Palantir Foundry equivalent:
      - Object type: "Location"
      - Primary key: location_id (string, MD5 hash of normalized address)
      - Properties: address, coordinates, incident count
      - Links TO: CensusTract (via within)
      - Links FROM: Incident (via occurs_at)
    
    Design note: canonical_lat/lon is the centroid of all incidents
    at this normalized address — it is a statistical property, not
    a geocoded ground truth.
    """
    location_id: str                        # MD5 hash of normalized_address
    normalized_address: str
    canonical_lat: Optional[float] = None
    canonical_lon: Optional[float] = None
    incident_count: Optional[int] = None
    tract_geoid: Optional[str] = None       # FK to CensusTract
    data_source: str = "Austin PD Crime Reports"
    load_timestamp: Optional[datetime] = None


@dataclass
class OffenseType:
    """
    Object Type: OffenseType
    
    A normalized crime classification. Bridges Austin PD's local
    offense codes to standard UCR categories.
    
    Palantir Foundry equivalent:
      - Object type: "Offense Type"
      - Primary key: offense_type_id (integer)
      - This is effectively a controlled vocabulary / taxonomy object
    """
    offense_type_id: int
    raw_description: str                    # Austin PD original string
    ucr_category: str                       # Normalized UCR category
    is_violent: bool                        # UCR Part 1 Violent Crime
    load_timestamp: Optional[datetime] = None


@dataclass
class Incident:
    """
    Object Type: Incident
    
    A single reported crime event, identified by APD case number.
    
    Palantir Foundry equivalent:
      - Object type: "Incident"
      - Primary key: incident_report_number (string)
      - Properties: temporal fields, clearance status, administrative fields
      - Links TO: Location (via occurs_at)
      - Links TO: OffenseType (via categorized_as)
      - Links TO: CensusTract (via tract_geoid, denormalized)
    
    Design note: We do not model Charge as a separate object type in
    this version. Austin PD records the "highest offense" per incident.
    A more complete ontology would have Charge with many-to-one → Incident.
    """
    incident_report_number: str
    occurred_dt: Optional[datetime] = None
    report_dt: Optional[datetime] = None
    hour_of_day: Optional[int] = None       # 0-23, derived from occurred_dt
    day_of_week: Optional[int] = None       # 0=Monday, derived from occurred_dt
    month: Optional[int] = None
    year: Optional[int] = None
    is_violent: Optional[bool] = None
    clearance_status: Optional[str] = None
    clearance_date: Optional[str] = None
    council_district: Optional[str] = None
    apd_sector: Optional[str] = None
    apd_district: Optional[str] = None
    # Link properties (materialized FKs)
    location_id: Optional[str] = None      # FK to Location
    offense_type_id: Optional[int] = None  # FK to OffenseType
    tract_geoid: Optional[str] = None      # FK to CensusTract
    data_source: str = "Austin PD Crime Reports"
    load_timestamp: Optional[datetime] = None


# ─────────────────────────────────────────────
# Link Types
# ─────────────────────────────────────────────

@dataclass
class LinkIncidentLocation:
    """
    Link Type: occurs_at
    Incident → Location
    """
    incident_report_number: str
    location_id: str
    link_type: str = "occurs_at"


@dataclass
class LinkLocationTract:
    """
    Link Type: within
    Location → CensusTract
    """
    location_id: str
    tract_geoid: str
    link_type: str = "within"
