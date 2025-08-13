# app/geocoding.py (Revised for Robustness)
"""
Utility for converting GPS coordinates into human-readable location names using a
fast, local lookup table. This adds valuable context for VLM prompting and UI display.
"""
from collections import Counter
import reverse_geocoder as rg
from pathlib import Path

# Define the data path relative to this script file.
# This ensures it works regardless of the current working directory.
# __file__ -> geocoding.py
# .parent -> app/
# .parent -> immich-album-suggester/
# / 'data' / 'cities15000.txt' -> immich-album-suggester/data/cities15000.txt
GEO_DATA_PATH = Path(__file__).parent.parent / 'data' / 'cities15000.txt'

# Initialize the geocoder once when the module is imported.
# This checks for the file on startup and is more efficient.
try:
    if not GEO_DATA_PATH.is_file():
        raise FileNotFoundError(f"Geocoding data file not found at {GEO_DATA_PATH}")
    # The library automatically uses this data file when it's in the search path.
    # By initializing it here, we force it to load and cache the data.
    _ = rg.search((0, 0))
    print("  - [GEO] Local geocoder initialized successfully.")
except Exception as e:
    print(f"  - [GEO-FATAL] Could not initialize local geocoder: {e}")
    # We can let the app continue, get_primary_location will just return None.
    pass

def get_primary_location(gps_coords: list[tuple[float, float]]) -> str | None:
    """
    Determines the most common country from a list of GPS coordinates using a
    fast, local reverse geocoder.
    """
    if not gps_coords:
        return None

    try:
        # The library performs a fast, vectorized lookup on all coordinates at once.
        results = rg.search(gps_coords)
        countries = [res['country'] for res in results if 'country' in res]
        
        if not countries:
            return None

        most_common_country = Counter(countries).most_common(1)[0][0]
        print(f"    - [GEO] Determined primary location: {most_common_country}")
        return most_common_country
    except Exception as e:
        # This will catch errors if initialization failed.
        print(f"    - [GEO-ERROR] Local reverse geocoding failed: {e}")
        return None