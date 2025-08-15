# app/models/__init__.py
"""
Models package for the Immich Album Suggester.

This package contains all data model definitions and DTOs for type-safe
data handling throughout the application.
"""

from .dto import (
    # Core DTOs
    SuggestionAlbum,
    PhotoAsset, 
    ImmichAlbum,
    VLMAnalysis,
    ClusteringCandidate,
    
    # Type aliases
    SuggestionStatus,
    AssetId,
    AlbumId,
    
    # Utility functions
    suggestion_from_db_row,
    photo_from_db_row,
    album_from_api_response,
)

__all__ = [
    # Core DTOs
    'SuggestionAlbum',
    'PhotoAsset',
    'ImmichAlbum', 
    'VLMAnalysis',
    'ClusteringCandidate',
    
    # Type aliases
    'SuggestionStatus',
    'AssetId',
    'AlbumId',
    
    # Utility functions
    'suggestion_from_db_row',
    'photo_from_db_row',
    'album_from_api_response',
]