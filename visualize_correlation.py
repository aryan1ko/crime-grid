import pandas as pd
import duckdb
import matplotlib.pyplot as plt
import seaborn as sns

con = duckdb.connect('data/austin_crime.duckdb')

try:
    # Using COUNT(*) to avoid the column name 'id' issue
    df = con.execute("""
        SELECT 
            t.tract_name,
            d.poverty_rate,
            d.median_household_income,
            d.total_population,
            COUNT(*) as incident_count
        FROM obj_census_tract t
        JOIN obj_demographics d ON t.geoid = d.geoid
        JOIN link_location_tract llt ON t.geoid = llt.geoid
        JOIN obj_incident i ON llt.location_id = i.location_id
        GROUP BY 1, 2, 3, 4
        HAVING d.total_population > 100
    """).df()

    df['crime_rate'] = (df['incident_count'] / df['total_population']) * 1000
    
    # Filter out the extreme downtown outliers to make the chart readable
    df_plot = df[df['crime_rate'] < 2000]

    sns.set_theme(style="whitegrid")
    plt.figure(figsize=(12, 7))
    
    # Create the scatter plot
    sns.scatterplot(
        data=df_plot, 
        x='poverty_rate', 
        y='crime_rate', 
        hue='median_household_income', 
        size='total_population',
        palette='viridis', 
        sizes=(40, 400), 
        alpha=0.6
    )

    plt.title('Austin Crime: Poverty vs. Density (By Census Tract)', fontsize=16, fontweight='bold')
    plt.xlabel('Poverty Rate (%)', fontsize=12)
    plt.ylabel('Incidents per 1,000 Residents', fontsize=12)
    
    # Add the regression line to visualize the 0.05 correlation (it will look flat)
    #TEMP:
    sns.regplot(data=df_plot, x='poverty_rate', y='crime_rate', scatter=False, color='red')
    # This adds a 'smooth' curve instead of a straight line


    plt.tight_layout()
    plt.savefig('data/outputs/correlation_plot.png')
    print("Success! Open data/outputs/correlation_plot.png to see the result.")

except Exception as e:
    print(f"Plotting failed: {e}")