# app/services/immich_service.py
"""
Provides a unified service for all interactions with the Immich instance.

This class is a façade that abstracts away the details of whether we are
communicating with the Immich PostgreSQL database (for efficient bulk reads)
or the Immich REST API (for writes and individual asset downloads).
"""
import logging
import pandas as pd
import requests
import time
from typing import Set, List
from .config_service import config
from .. import immich_db, immich_api
from ..exceptions import ImmichDBError, ImmichAPIError

logger = logging.getLogger(__name__)

class ImmichService:
    def __init__(self):
        # The service is initialized once with the application's configuration.
        self._sdk_config = {
            'immich': {
                'url': config.immich_url,
                'api_key': config.immich_api_key,
                'api_timeout_seconds': config.get('immich.api_timeout_seconds', 30)
            }
        }
        
        # Cache for album asset IDs to avoid hammering the API
        self._album_assets_cache: Set[str] = set()
        self._album_assets_cache_time: float = 0
        self._cache_ttl_seconds = config.get('immich.album_cache_ttl_seconds', 300)  # 5 minutes default
        
        try:
            self.api_client = immich_api.get_api_client(self._sdk_config)
            logger.info("Immich API client initialized successfully.")
        except Exception as e:
            logger.critical("Failed to initialize Immich API client.", exc_info=True)
            raise ImmichAPIError("Could not initialize Immich API client.") from e

    def fetch_assets_for_clustering(self, excluded_ids: list[str]) -> pd.DataFrame:
        """
        Fetches all asset metadata and embeddings required for clustering.
        This operation uses a direct, read-only PostgreSQL connection for performance.

        Args:
            excluded_ids: A list of asset IDs to exclude from the query.

        Returns:
            A pandas DataFrame containing the asset data.
        
        Raises:
            ImmichDBError: If the database query fails.
        """
        logger.info(f"Fetching assets for clustering, excluding {len(excluded_ids)} IDs.")
        try:
            pg_conn = immich_db.get_connection()
            # The fetch_assets function from the original module handles its own connection closing.
            df = immich_db.fetch_assets(pg_conn, config.yaml, excluded_ids)
            logger.info(f"Successfully fetched {len(df)} new assets from Immich DB.")
            return df
        except Exception as e:
            logger.error("Failed to fetch assets via direct DB connection.", exc_info=True)
            # Chain the original exception for full context.
            raise ImmichDBError("A failure occurred while fetching assets from the Immich database.") from e

    def get_thumbnail_bytes(self, asset_id: str) -> bytes | None:
        """
        Downloads the thumbnail for a single asset via the Immich API.
        Returns image bytes or None if the download fails. This is designed to be
        resilient for UI display, where a missing thumbnail is not a fatal error.

        Args:
            asset_id: The ID of the asset to fetch.

        Returns:
            The image content as bytes, or None if download fails.
        """

        try:
            # The download_and_convert_image function has its own robust retry logic.
            return immich_api.download_and_convert_image(self.api_client, asset_id, config.yaml)
        except Exception as e:
            # Even if the underlying function has retries, we log any final, unhandled failure.
            logger.warning(f"Final attempt to download thumbnail for asset {asset_id} failed.", exc_info=True)
            return None
    
    def get_full_image_bytes(self, asset_id: str) -> bytes | None:
        """
        Downloads the full-size original image for a single asset via the Immich API.
        Returns image bytes or None if the download fails.

        Args:
            asset_id: The ID of the asset to fetch.

        Returns:
            The full image content as bytes, or None if download fails.
        """
        try:
            return immich_api.download_full_image(self.api_client, asset_id, config.yaml)
        except Exception as e:
            logger.warning(f"Failed to download full image for asset {asset_id}.", exc_info=True)
            return None
            
    def get_exif_data(self, asset_id: str) -> dict | None:
        """
        Fetches EXIF data for a single asset via direct DB connection.

        Args:
            asset_id: The ID of the asset to fetch EXIF data for.

        Returns:
            A dictionary of EXIF data, or None if not found.
        """
        logger.debug(f"Fetching EXIF for asset {asset_id}.")
        try:
            # get_exif_for_asset handles its own connection.
            return immich_db.get_exif_for_asset(config.yaml, asset_id)
        except Exception as e:
            logger.error(f"Failed to fetch EXIF data for asset {asset_id}.", exc_info=True)
            raise ImmichDBError(f"Could not fetch EXIF for asset {asset_id}.") from e

    def create_album(self, title: str, asset_ids: list[str], cover_asset_id: str, highlight_ids: list[str]) -> bool:
        """
        Creates a new album in Immich via its official API.

        Args:
            title: The desired title of the new album.
            asset_ids: A list of all asset IDs to include in the album.
            cover_asset_id: The asset ID to be set as the album cover.
            highlight_ids: A list of asset IDs to mark as favorites within the album.

        Returns:
            True on success, False on failure.
        
        Raises:
            ImmichAPIError: If the API call fails unexpectedly.
        """
        logger.info(f"Attempting to create album '{title}' with {len(asset_ids)} assets in Immich.")
        try:
            success = immich_api.create_immich_album(
                api_client=self.api_client,
                title=title,
                asset_ids=asset_ids,
                cover_asset_id=cover_asset_id,
                highlight_ids=highlight_ids
            )
            if not success:
                # The underlying function prints detailed errors, but we log it here too.
                logger.error(f"Call to create_immich_album for '{title}' returned False.")
            else:
                # Clear the album cache since we just created a new album
                self.clear_album_cache()
                logger.debug(f"Cleared album cache after creating album '{title}'")
            return success
        except Exception as e:
            logger.error(f"An unexpected exception occurred while creating album '{title}'.", exc_info=True)
            raise ImmichAPIError("An API call to create an album failed unexpectedly.") from e

    def get_all_asset_ids_in_albums(self, force_refresh: bool = False) -> Set[str]:
        """
        Fetches all asset IDs that are currently in any Immich album.
        
        This method prevents the album suggester from creating duplicate albums
        for photos that are already organized in manually created albums.
        Results are cached for performance to avoid hammering the API.
        
        Args:
            force_refresh: If True, bypasses cache and forces fresh API call
            
        Returns:
            A set of asset IDs that are currently in albums
            
        Raises:
            ImmichAPIError: If the API call fails
        """
        current_time = time.time()
        
        # Check cache validity
        if (not force_refresh and 
            self._album_assets_cache and 
            (current_time - self._album_assets_cache_time) < self._cache_ttl_seconds):
            logger.debug(f"Using cached album assets ({len(self._album_assets_cache)} assets)")
            return self._album_assets_cache.copy()
        
        logger.info("Fetching all albums from Immich to build exclusion list")
        
        try:
            # Use the same API base URL logic as the SDK client
            from .. import immich_api
            api_base_url = immich_api._build_api_base(config.immich_url)
            api_key = config.immich_api_key
            
            headers = {
                'x-api-key': api_key,
                'Accept': 'application/json'
            }
            
            # Use the /albums endpoint to get all albums
            albums_url = f"{api_base_url}/albums"
            timeout = self._sdk_config['immich']['api_timeout_seconds']
            
            response = requests.get(albums_url, headers=headers, timeout=timeout)
            response.raise_for_status()
            
            albums_data = response.json()
            logger.info(f"Retrieved {len(albums_data)} albums from Immich")
            
            # Extract all unique asset IDs from all albums
            asset_ids = set()
            total_assets = 0
            
            for album in albums_data:
                album_name = album.get('albumName', 'Unknown')
                album_assets = album.get('assets', [])
                
                # Extract asset IDs from the assets array
                for asset in album_assets:
                    asset_id = asset.get('id')
                    if asset_id:
                        asset_ids.add(asset_id)
                        total_assets += 1
                
                logger.debug(f"Album '{album_name}': {len(album_assets)} assets")
            
            logger.info(f"Found {len(asset_ids)} unique assets across {len(albums_data)} albums")
            
            # Update cache
            self._album_assets_cache = asset_ids
            self._album_assets_cache_time = current_time
            
            return asset_ids.copy()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch albums from Immich API: {e}", exc_info=True)
            raise ImmichAPIError(f"Could not retrieve albums from Immich: {e}") from e
        except (KeyError, ValueError) as e:
            logger.error(f"Unexpected response format from albums API: {e}", exc_info=True)
            raise ImmichAPIError(f"Invalid response format from albums API: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error while fetching album assets: {e}", exc_info=True)
            raise ImmichAPIError(f"Unexpected error fetching album assets: {e}") from e

    def clear_album_cache(self) -> None:
        """
        Clears the cached album asset IDs.
        
        This can be useful after creating new albums or when you want to ensure
        fresh data on the next call to get_all_asset_ids_in_albums().
        """
        self._album_assets_cache.clear()
        self._album_assets_cache_time = 0
        logger.debug("Album assets cache cleared")

# Singleton instance
immich_service = ImmichService()