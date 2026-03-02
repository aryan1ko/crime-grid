import pandas as pd
import duckdb

con = duckdb.connect('data/austin_crime.duckdb')

def find_col(cols, keyword):
    """Helper to find a column name containing a keyword (case-insensitive)"""
    for c in cols:
        if keyword.lower() in c.lower():
            return c
    return None

try:
    # 1. Inspect the demographics table
    demo_cols = [c[1] for c in con.execute("PRAGMA table_info(obj_demographics)").fetchall()]
    
    # 2. Map the columns dynamically
    poverty_col = find_col(demo_cols, 'poverty')
    income_col = find_col(demo_cols, 'income')
    pop_col = find_col(demo_cols, 'population')

    print(f"DEBUG: Found Columns -> Poverty: {poverty_col}, Income: {income_col}, Pop: {pop_col}")

    # 3. Execute the final query using the discovered names
    df = con.execute(f"""
        SELECT 
            t.tract_name,
            d.{poverty_col} as poverty_rate,
            d.{income_col} as median_income,
            d.{pop_col} as total_population,
            COUNT(i.location_id) as incident_count
        FROM obj_census_tract t
        JOIN obj_demographics d ON t.geoid = d.geoid
        JOIN link_location_tract llt ON t.geoid = llt.geoid
        JOIN obj_incident i ON llt.location_id = i.location_id
        GROUP BY 1, 2, 3, 4
        HAVING d.{pop_col} > 100
    """).df()

    # Calculate and Print
    df['crime_rate'] = (df['incident_count'] / df['total_population']) * 1000
    poverty_corr = df['poverty_rate'].corr(df['crime_rate'])
    
    print(f"\n--- 📈 Austin Socioeconomic Correlation ---")
    print(f"Tracts Analyzed: {len(df)}")
    print(f"Poverty vs Crime Rate: {poverty_corr:.4f}")
    print(f"Median Income vs Crime Rate: {df['median_income'].corr(df['crime_rate']):.4f}")

except Exception as e:
    print(f"Analysis failed: {e}")
    print(f"Available columns in obj_demographics: {demo_cols}")