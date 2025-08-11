# app/geocoding.py
"""
Utility for converting GPS coordinates into human-readable location names.
This adds valuable context for VLM prompting and UI display.
"""
import random
from collections import Counter
from geopy.geocoders import Nominatim

def get_primary_location(gps_coords: list, config: dict) -> str | None:
    """
    Determines the most common country from a list of GPS coordinates.
    It samples a small subset of coordinates to avoid excessive API calls.

    
    Args:
        gps_coords: A list of (latitude, longitude) tuples.
        
    Returns:
        The name of the most common country, or None if not determinable.
    """
    if not gps_coords:
        return None

    print("    - [GEO] Performing reverse geocoding...")
    cfg = config['geocoding']
    geolocator = Nominatim(user_agent=cfg['user_agent'])
    countries = []
    
    # Sample up to 5 coordinates to be efficient and respectful of API limits.
    sample_size = min(len(gps_coords), cfg['sample_size'])
    sample_coords = random.sample(gps_coords, sample_size)

    for lat, lon in sample_coords:
        try:
            location = geolocator.reverse((lat, lon), language='en', timeout=cfg['api_timeout_seconds'])
            if location and 'address' in location.raw:
                country = location.raw['address'].get('country')
                if country:
                    countries.append(country)
        except Exception:
            # Fail silently for individual lookup errors.
            continue
    
    if not countries:
        return None
    
    # Return the most frequently found country name.
    most_common_country = Counter(countries).most_common(1)[0][0]
    print(f"    - [GEO] Determined primary location: {most_common_country}")
    return most_common_country