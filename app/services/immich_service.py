# app/services/immich_service.py
"""
Provides a unified service for all interactions with the Immich instance.

This class is a façade that abstracts away the details of whether we are
communicating with the Immich PostgreSQL database (for efficient bulk reads)
or the Immich REST API (for writes and individual asset downloads).
"""
import logging
import pandas as pd
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
            return success
        except Exception as e:
            logger.error(f"An unexpected exception occurred while creating album '{title}'.", exc_info=True)
            raise ImmichAPIError("An API call to create an album failed unexpectedly.") from e

# Singleton instance
immich_service = ImmichService()