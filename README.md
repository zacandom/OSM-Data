# OSM-Data
Within this repository are files to collect all historical religious and conflict related sites in the following countries: 
 Austria, Belgium, Bulgaria, Czech Republic, Denmark, Finland,
 France, Germany, Greece, Hungary, Italy, Luxembourg,
 Netherlands, Norway, Poland, Portugal, Romania, Slovakia,
 Spain, Sweden, Switzerland, and the United Kingdom.

 Site data is drawn from OpenStreetMaps.

 The following packages are required to run OSMReligion.py and OSMConflict.py
 
| Package | Purpose | Minimum Version |
| osmnx | Downloads and handles OpenStreetMap data (geocoding, Overpass API queries) | ≥ 2.0.6 |
| geopandas | Spatial data manipulation and integration with pandas | ≥ 0.14 |
| shapely | Geometry operations for polygons, intersections, and centroids | ≥ 2.0 |
| pandas | Data manipulation and tabular export | ≥ 2.0 |
| openpyxl | Excel writer engine for `pandas.to_excel()` | ≥ 3.1 |
| concurrent.futures | Standard library for parallel processing | built-in |
| time, random, os, re | Standard Python libraries for I/O, timing, and regex operations | built-in |

Installation

To install all dependencies at once, run:

```bash
pip install -U "osmnx>=2.0.6" geopandas shapely pandas openpyxl
```
Script details:

The following scripts are to be run on Spyder:

OSMReligion.py automatically downloads and organizes data on religious historic sites across Europe using the [OpenStreetMap (OSM)](https://www.openstreetmap.org/) Overpass API.

It performs the following steps for each country:

1. Geocodes the national boundary using OSM’s Nominatim service.  
2. Divides the country into small geographic tiles (default: `0.8°` square) to prevent Overpass query overloads.  
3. Fetches OSM features within each tile that are tagged as both *historic* and *religious*, such as:
   - `historic=church`, `historic=abbey`, `historic=temple`, etc.
   - `building=cathedral`, `building=mosque`, `building=monastery`
   - `amenity=place_of_worship` combined with `historic=yes`
4. Extracts key metadata from each feature, including:
   - `name`
   - `site_type` (e.g. `historic:church`, `historic_building:abbey`)
   - `civilization` (if available, e.g. `Roman`, `Byzantine`, `Ottoman`)
   - `latitude` and `longitude` (based on centroid coordinates)
   - `wikidata` and `wikipedia` references when present
5. Cleans and deduplicates the dataset across all tiles for that country.  
6. Exports the final results to a country-specific Excel file.

All outputs are saved in the folder "europe_religious_historic_sites_tiled", under the name (country)_religious_historic_sites_tiled.xlsx

Similarly, OSMConflict.py retrieves and organizes data on historical sites of conflict across Europe using the [OpenStreetMap (OSM)](https://www.openstreetmap.org/) Overpass API.

It identifies and exports battlefields, war memorials, fortifications, bunkers, trenches, and other historic conflict-related locations for the same set of 21 European countries.

For each country, the script:

1. Geocodes the country boundary using OSM’s Nominatim API.  
2. Divides the country into smaller tiles (default: `0.8°`) to avoid Overpass timeouts.  
3. Queries OSM for features tagged with:
   - `historic=battlefield`, `historic=war_memorial`, `historic=fort`, `historic=trench`, etc.  
   - `military=*` and `landuse=military`  
   - Plus keyword-matched features mentioning “war,” “battle,” “siege,” “WWI,” “WWII,” etc.
4. Filters and classifies results into conflict-related types:
   - `battlefield`
   - `war_memorial`
   - `fortification_or_military_site`
   - `historic_conflict_feature`, etc.
5. Extracts key metadata:
   - `name`
   - `conflict_type`
   - `latitude` / `longitude`
   - `wikidata` and `wikipedia` references (if available)
6. Exports cleaned and deduplicated results to an Excel file for each country.


All outputs are saved in the folder "europe_conflict_historic_sites_tiled", nder the name (country)_conflict_historic_sites_tiled.xlsx

