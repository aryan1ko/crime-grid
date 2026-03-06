#!/bin/bash
# push_to_github.sh
# Run this after creating your GitHub repo to push everything.
# Usage: bash push_to_github.sh YOUR_GITHUB_USERNAME

set -e

USERNAME=${1:-"YOUR_GITHUB_USERNAME"}
REPO_NAME="austin-crime-ontology"

echo "Initializing git repo..."
git init
git add .
git commit -m "Initial commit: Austin Crime Ontology

Palantir Foundry-style ontology built on Austin PD open data.
- 6 object types: Incident, Location, OffenseType, District, CensusTract, Demographics
- 5 link types with spatial join for Location → CensusTract
- Entity resolution with 25m deduplication threshold
- DuckDB analytical layer with 6 cross-object queries
- Interactive Folium maps (choropleth, hotspot, trend)
- Full ontology design document"

echo ""
echo "Now create a new repo on GitHub: https://github.com/new"
echo "Name it: ${REPO_NAME}"
echo "Then run:"
echo ""
echo "  git remote add origin https://github.com/${USERNAME}/${REPO_NAME}.git"
echo "  git branch -M main"
echo "  git push -u origin main"
echo ""
echo "Done! Your repo will be at: https://github.com/${USERNAME}/${REPO_NAME}"
