"""
Manages all interactions with the Immich API for actions like creating
albums, adding photos, and downloading thumbnails for VLM analysis.
"""

import immich_python_sdk
import requests
from PIL import Image
from io import BytesIO
import os
import sys

def _normalize_host(host: str) -> str:
    """
    Ensure the Immich host is the root (no trailing '/api'), no trailing slash.
    """
    if not host:
        return host
    h = host.strip().rstrip('/')
    if h.lower().endswith('/api'):
        h = h[:-4]
        h = h.rstrip('/')
    return h

def _build_api_base(host: str) -> str:
    """
    Returns the API base URL (root + '/api'), exactly once.
    """
    root = _normalize_host(host)
    return f"{root}/api"

def get_api_client(config: dict) -> immich_python_sdk.ApiClient:
    """Initializes and returns the Immich SDK API client."""
    immich_cfg = (config or {}).get('immich', {}) if isinstance(config, dict) else {}
    host = immich_cfg.get('url') or os.getenv('IMMICH_URL')
    api_key = immich_cfg.get('api_key') or os.getenv('IMMICH_API_KEY')
    if not host or not api_key:
        raise ValueError("Immich API configuration is missing. Set immich.url and immich.api_key in config.yaml or provide IMMICH_URL and IMMICH_API_KEY environment variables.")
    # Important: SDK should get the root (no '/api') to avoid double '/api'
    configuration = immich_python_sdk.Configuration(host=_normalize_host(host))
    configuration.api_key['api_key'] = api_key
    return immich_python_sdk.ApiClient(configuration)

def download_and_convert_image(api_client: immich_python_sdk.ApiClient, asset_id: str, config: dict) -> bytes | None:

    host = immich_cfg.get('url') or os.getenv('IMMICH_URL')
    api_key = immich_cfg.get('api_key') or os.getenv('IMMICH_API_KEY')
    if not host or not api_key:
        raise ValueError("Immich API configuration is missing. Set immich.url and immich.api_key in config.yaml or provide IMMICH_URL and IMMICH_API_KEY environment variables.")
    configuration = immich_python_sdk.Configuration(host=host)
    configuration.api_key['api_key'] = api_key
    return immich_python_sdk.ApiClient(configuration)

def download_and_convert_image(api_client: immich_python_sdk.ApiClient, asset_id: str, config: dict) -> bytes | None:
    """
    Downloads a thumbnail for a given asset ID and converts it to JPEG format
    in memory. This robust function handles the specific way Immich serves

    thumbnails (often as WebP regardless of request headers).

    Returns:
        JPEG image data as bytes, or None if download/conversion fails.
    """
    immich_url = api_client.configuration.host
    api_key = api_client.configuration.api_key['api_key']
    headers = {'x-api-key': api_key, 'Accept': 'image/jpeg,image/webp,*/*'}
    api_base = _build_api_base(immich_url)

    # Try both common URL patterns across Immich versions:
    candidate_urls = [
        f"{api_base}/asset/thumbnail/{asset_id}",   # singular 'asset'
        f"{api_base}/assets/{asset_id}/thumbnail",  # plural 'assets'
    ]

    try:
        last_exc = None
        for thumbnail_url in candidate_urls:
            try:
                response = requests.get(thumbnail_url, headers=headers, stream=True, timeout=config['immich']['api_timeout_seconds'])
                if response.status_code == 404:
                    # Try the next candidate
                    continue
                response.raise_for_status()

                # Convert to RGB and save as JPEG in a memory buffer.
                image = Image.open(BytesIO(response.content)).convert("RGB")
                jpeg_buffer = BytesIO()
                image.save(jpeg_buffer, format="JPEG")
                return jpeg_buffer.getvalue()
            except requests.exceptions.RequestException as e:
                last_exc = e
                # For non-404 errors, break (network/auth/etc)
                if not (hasattr(e, 'response') and e.response is not None and e.response.status_code == 404):
                    break

        if last_exc is not None:
            raise last_exc
        else:
            # No candidate worked but no exception captured (unlikely)
            print(f"    - [API-WARN] No thumbnail URL variant worked for asset {asset_id}. Tried: {candidate_urls}", file=sys.stderr)
            return None
    
    except requests.exceptions.RequestException as e:
        tried = " | ".join(candidate_urls)
        print(f"    - [API-WARN] Error downloading asset {asset_id} thumbnail. Tried: {tried}. Error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"    - [API-WARN] Failed to convert image for asset {asset_id}: {e}", file=sys.stderr)
        
    return None

def create_immich_album(api_client: immich_python_sdk.ApiClient, title: str, asset_ids: list, cover_asset_id: str, highlight_ids: list):
    """
    Creates a complete album in Immich, including adding assets, setting a
    cover photo, and favoriting highlights.
    """
    print(f"  - [API] Attempting to create album: '{title}'")
    try:
        albums_api = immich_python_sdk.AlbumApi(api_client)
        asset_api = immich_python_sdk.AssetApi(api_client)
        
        # 1. Create the album
        create_dto = immich_python_sdk.CreateAlbumDto(album_name=title)
        album = albums_api.create_album(create_album_dto=create_dto)
        print(f"    - Album '{title}' created with ID: {album.id}")
        
        # 2. Add assets to the album
        add_dto = immich_python_sdk.AddAssetsDto(asset_ids=asset_ids)
        albums_api.add_assets_to_album(id=album.id, add_assets_dto=add_dto)
        print(f"    - Added {len(asset_ids)} assets.")
        
        # 3. Set the cover photo
        if cover_asset_id and cover_asset_id in asset_ids:
            update_dto = immich_python_sdk.UpdateAlbumDto(album_thumbnail_id=cover_asset_id)
            albums_api.update_album(id=album.id, update_album_dto=update_dto)
            print(f"    - Set asset {cover_asset_id} as album cover.")
            
        # 4. Favorite the highlight photos
        if highlight_ids:
            update_asset_dto = immich_python_sdk.UpdateAssetDto(is_favorite=True)
            for asset_id in highlight_ids:
                if asset_id in asset_ids:
                    asset_api.update_asset(id=asset_id, update_asset_dto=update_asset_dto)
            print(f"    - Favorited {len(highlight_ids)} highlight assets.")
        
        return True
    
    except immich_python_sdk.ApiException as e:
        print(f"  - [API-ERROR] Failed to create album '{title}'. Reason: {e.reason}", file=sys.stderr)
        return False