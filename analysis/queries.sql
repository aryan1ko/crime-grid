-- analysis/queries.sql
--
-- Cross-object analytical queries that demonstrate the ontology's value.
-- Each query would be impossible (or very ugly) against the raw flat CSV.
-- Run these in DuckDB after completing the pipeline.
--
-- Usage: duckdb data/austin_crime.duckdb < analysis/queries.sql

-- ══════════════════════════════════════════════════════════════════
-- QUERY 1: Violent crime rate per capita by census tract
-- 
-- Crosses: Incident → CensusTract → Demographics
-- This query is the core of the "cross-object" value proposition.
-- You can't run it against raw APD data alone — you need the Census join.
-- ══════════════════════════════════════════════════════════════════

SELECT
    ct.geoid,
    ct.total_population,
    ct.poverty_rate,
    ct.median_household_income,
    ct.pct_hispanic,
    COUNT(i.incident_report_number)                         AS total_incidents,
    SUM(i.is_violent::int)                                  AS violent_incidents,
    ROUND(SUM(i.is_violent::int) * 10000.0 / NULLIF(ct.total_population, 0), 1)
                                                            AS violent_rate_per_10k,
    ROUND(COUNT(*) * 10000.0 / NULLIF(ct.total_population, 0), 1)
                                                            AS total_rate_per_10k
FROM incident i
JOIN census_tract ct ON i.tract_geoid = ct.geoid
WHERE i.year BETWEEN 2018 AND 2023
GROUP BY ct.geoid, ct.total_population, ct.poverty_rate,
         ct.median_household_income, ct.pct_hispanic
HAVING ct.total_population > 500
ORDER BY violent_rate_per_10k DESC
LIMIT 20;


-- ══════════════════════════════════════════════════════════════════
-- QUERY 2: Year-over-year violent crime trend by tract quartile
--
-- Ranks tracts by poverty rate, shows YoY violent crime trend per quartile.
-- Shows whether high-poverty tracts saw different COVID-era crime patterns.
-- ══════════════════════════════════════════════════════════════════

WITH tract_quartiles AS (
    SELECT
        geoid,
        total_population,
        poverty_rate,
        NTILE(4) OVER (ORDER BY poverty_rate)   AS poverty_quartile
    FROM census_tract
    WHERE total_population > 200
),
annual_by_quartile AS (
    SELECT
        tq.poverty_quartile,
        i.year,
        COUNT(i.incident_report_number)         AS incidents,
        SUM(i.is_violent::int)                  AS violent_incidents,
        SUM(tq.total_population)                AS population
    FROM incident i
    JOIN tract_quartiles tq ON i.tract_geoid = tq.geoid
    WHERE i.year BETWEEN 2018 AND 2023
    GROUP BY tq.poverty_quartile, i.year
)
SELECT
    poverty_quartile,
    year,
    incidents,
    violent_incidents,
    ROUND(violent_incidents * 10000.0 / population, 2) AS violent_rate_per_10k
FROM annual_by_quartile
ORDER BY poverty_quartile, year;


-- ══════════════════════════════════════════════════════════════════
-- QUERY 3: Repeat incident locations (hotspot seed candidates)
--
-- Which locations have the most incidents?
-- Links: Location → Incident, Location → CensusTract
-- ══════════════════════════════════════════════════════════════════

SELECT
    l.normalized_address,
    l.canonical_lat,
    l.canonical_lon,
    ct.geoid                                AS tract_geoid,
    ct.poverty_rate,
    COUNT(i.incident_report_number)         AS incident_count,
    SUM(i.is_violent::int)                  AS violent_count,
    MIN(i.occurred_dt)                      AS first_incident,
    MAX(i.occurred_dt)                      AS last_incident,
    COUNT(DISTINCT i.year)                  AS years_active
FROM location l
JOIN incident i      ON l.location_id = i.location_id
LEFT JOIN census_tract ct ON l.tract_geoid = ct.geoid
GROUP BY l.location_id, l.normalized_address, l.canonical_lat,
         l.canonical_lon, ct.geoid, ct.poverty_rate
HAVING COUNT(*) >= 10
ORDER BY incident_count DESC
LIMIT 50;


-- ══════════════════════════════════════════════════════════════════
-- QUERY 4: Offense type temporal heatmap (hour × day_of_week)
--
-- For a given UCR category, when does it most commonly occur?
-- Useful for patrol resource allocation.
-- ══════════════════════════════════════════════════════════════════

SELECT
    ot.ucr_category,
    i.day_of_week,
    i.hour_of_day,
    COUNT(*)    AS incident_count
FROM incident i
JOIN offense_type ot ON i.offense_type_id = ot.offense_type_id
WHERE i.year >= 2019
  AND ot.ucr_category IN ('Robbery', 'Burglary', 'Larceny-Theft', 'Aggravated Assault')
GROUP BY ot.ucr_category, i.day_of_week, i.hour_of_day
ORDER BY ot.ucr_category, i.day_of_week, i.hour_of_day;


-- ══════════════════════════════════════════════════════════════════
-- QUERY 5: Address quality analysis
--
-- What fraction of locations have high coordinate variance?
-- High variance = inconsistent APD reporting for the same address.
-- This is an ontology health check — it tells us how much to trust
-- our canonical coordinates.
-- ══════════════════════════════════════════════════════════════════

SELECT
    l.location_id,
    l.normalized_address,
    l.canonical_lat,
    l.canonical_lon,
    COUNT(i.incident_report_number)             AS sample_size,
    STDDEV(i.latitude)                          AS lat_stddev,
    STDDEV(i.longitude)                         AS lon_stddev,
    -- ~0.00001 degrees ≈ 1 meter
    CASE
        WHEN STDDEV(i.latitude) > 0.001 THEN 'HIGH_VARIANCE'
        WHEN STDDEV(i.latitude) > 0.0001 THEN 'MEDIUM_VARIANCE'
        ELSE 'LOW_VARIANCE'
    END                                         AS coordinate_quality
FROM location l
JOIN incident i ON l.location_id = i.location_id
WHERE i.latitude IS NOT NULL
GROUP BY l.location_id, l.normalized_address, l.canonical_lat, l.canonical_lon
HAVING COUNT(*) >= 5
ORDER BY lat_stddev DESC NULLS LAST
LIMIT 30;


-- ══════════════════════════════════════════════════════════════════
-- QUERY 6: Clearance rate by offense type and tract poverty quartile
--
-- Do high-poverty tracts have lower clearance rates for violent crimes?
-- Clearance = case solved (arrest made or exceptional clearance).
-- ══════════════════════════════════════════════════════════════════

WITH tract_quartiles AS (
    SELECT geoid, NTILE(4) OVER (ORDER BY poverty_rate) AS poverty_quartile
    FROM census_tract WHERE total_population > 200
)
SELECT
    tq.poverty_quartile,
    ot.ucr_category,
    COUNT(*)                                                AS total,
    SUM(CASE WHEN i.clearance_status NOT IN ('N', '') AND i.clearance_status IS NOT NULL
             THEN 1 ELSE 0 END)                            AS cleared,
    ROUND(
        100.0 * SUM(CASE WHEN i.clearance_status NOT IN ('N', '') AND i.clearance_status IS NOT NULL
                         THEN 1 ELSE 0 END) / COUNT(*), 1
    )                                                       AS clearance_rate_pct
FROM incident i
JOIN offense_type ot     ON i.offense_type_id = ot.offense_type_id
JOIN tract_quartiles tq  ON i.tract_geoid = tq.geoid
WHERE ot.is_violent = TRUE
  AND i.year BETWEEN 2018 AND 2023
GROUP BY tq.poverty_quartile, ot.ucr_category
ORDER BY ot.ucr_category, tq.poverty_quartile;
