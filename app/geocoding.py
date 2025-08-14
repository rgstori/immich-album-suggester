# app/geocoding.py (Revised for Robustness)
"""
Utility for converting GPS coordinates into human-readable location names using a
fast, local lookup table. This adds valuable context for VLM prompting and UI display.
"""
from collections import Counter
import reverse_geocoder as rg
from pathlib import Path
import pycountry
import logging

logger = logging.getLogger(__name__)

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
    logger.info("Local geocoder initialized successfully")
except Exception as e:
    logger.critical(f"Could not initialize local geocoder: {e}")
    # We can let the app continue, get_primary_location will just return None.
    pass


def _get_country_name(country_code: str) -> str:
    """
    Convert a 2-letter country code to full country name using pycountry.
    
    Args:
        country_code: 2-letter ISO country code (e.g., 'IT', 'GB', 'AU')
        
    Returns:
        Full country name or the original code if lookup fails
    """
    if not country_code:
        return country_code
        
    try:
        country = pycountry.countries.get(alpha_2=country_code)
        if country:
            return country.name
    except Exception:
        pass
    
    # Return the original code if lookup fails
    return country_code

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
        
        # Extract country information
        countries = []
        for res in results:
            if 'cc' in res and res['cc']:
                country_code = res['cc']
                country_name = _get_country_name(country_code)
                countries.append(country_name)
        
        if not countries:
            return None

        most_common_country = Counter(countries).most_common(1)[0][0]
        logger.debug(f"Determined primary location: {most_common_country}")
        return most_common_country
    except Exception as e:
        # This will catch errors if initialization failed.
        logger.error(f"Local reverse geocoding failed: {e}")
        return None


def get_location_from_coordinates(lat: float, lon: float) -> str | None:
    """
    Gets city and country from a single set of GPS coordinates.
    
    Args:
        lat: Latitude
        lon: Longitude
        
    Returns:
        Formatted location string like "Venice, Italy" or None if lookup fails
    """
    if not lat or not lon:
        return None
        
    try:
        # Search for the single coordinate
        results = rg.search([(lat, lon)])
        if results and len(results) > 0:
            result = results[0]
            city = result.get('name', '')
            country_code = result.get('cc', '')  # country code
            
            country_display = _get_country_name(country_code) if country_code else ''
            
            if city and country_display:
                return f"{city}, {country_display}"
            elif country_display:
                return country_display
                
        return None
    except Exception as e:
        logger.error(f"Failed to geocode coordinates ({lat}, {lon}): {e}")
        return None