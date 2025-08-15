# app/models/dto.py
"""
Data Transfer Objects (DTOs) for type-safe data handling throughout the application.

This module provides strongly typed data classes to replace Dict[str, Any] usage,
offering better IDE support, static analysis, and runtime validation.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal, Union
import json
import logging

logger = logging.getLogger(__name__)

# Type aliases for better readability
SuggestionStatus = Literal['pending', 'approved', 'rejected', 'enriching', 'enrichment_failed', 'pending_enrichment', 'from_immich']
AssetId = str
AlbumId = str

@dataclass
class PhotoAsset:
    """Represents a photo asset with metadata."""
    id: AssetId
    file_path: Optional[str] = None
    file_created_at: Optional[datetime] = None
    date_time_original: Optional[datetime] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    orientation: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    clip_embedding: Optional[List[float]] = None
    
    @property
    def location(self) -> Optional[str]:
        """Get formatted location string from available components."""
        parts = [self.city, self.state, self.country]
        location_parts = [part for part in parts if part]
        return ', '.join(location_parts) if location_parts else None
    
    @property
    def primary_date(self) -> Optional[datetime]:
        """Get the primary date, preferring date_time_original."""
        return self.date_time_original or self.file_created_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for database operations."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PhotoAsset:
        """Create PhotoAsset from dictionary data."""
        # Handle datetime fields
        for date_field in ['file_created_at', 'date_time_original']:
            if data.get(date_field) and isinstance(data[date_field], str):
                try:
                    data[date_field] = datetime.fromisoformat(data[date_field].replace('Z', '+00:00'))
                except ValueError:
                    logger.warning(f"Could not parse date field {date_field}: {data[date_field]}")
                    data[date_field] = None
        
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

@dataclass
class SuggestionAlbum:
    """Represents an album suggestion with all metadata."""
    id: Optional[int] = None
    status: SuggestionStatus = 'pending_enrichment'
    created_at: Optional[datetime] = None
    event_start_date: Optional[datetime] = None
    event_end_date: Optional[datetime] = None
    location: Optional[str] = None
    vlm_title: Optional[str] = None
    vlm_description: Optional[str] = None
    strong_asset_ids: List[AssetId] = field(default_factory=list)
    weak_asset_ids: List[AssetId] = field(default_factory=list)
    cover_asset_id: Optional[AssetId] = None
    immich_album_id: Optional[AlbumId] = None
    additional_asset_ids: List[AssetId] = field(default_factory=list)
    
    @property
    def all_asset_ids(self) -> List[AssetId]:
        """Get all asset IDs (strong + weak + additional)."""
        return self.strong_asset_ids + self.weak_asset_ids + self.additional_asset_ids
    
    @property
    def total_asset_count(self) -> int:
        """Get total count of all assets."""
        return len(self.strong_asset_ids) + len(self.weak_asset_ids) + len(self.additional_asset_ids)
    
    @property
    def is_from_immich(self) -> bool:
        """Check if this is an existing Immich album."""
        return self.status == 'from_immich'
    
    @property
    def needs_enrichment(self) -> bool:
        """Check if suggestion needs VLM enrichment."""
        return self.status in ['pending_enrichment', 'enrichment_failed']
    
    @property
    def is_ready_for_review(self) -> bool:
        """Check if suggestion is ready for user review."""
        return self.status == 'pending'
    
    @property
    def has_additions(self) -> bool:
        """Check if there are additional assets that can be added."""
        return len(self.additional_asset_ids) > 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for database operations."""
        data = asdict(self)
        
        # Convert list fields to JSON strings for database storage
        data['strong_asset_ids_json'] = json.dumps(self.strong_asset_ids)
        data['weak_asset_ids_json'] = json.dumps(self.weak_asset_ids)
        data['additional_asset_ids_json'] = json.dumps(self.additional_asset_ids)
        
        # Remove the list versions since database expects JSON strings
        del data['strong_asset_ids']
        del data['weak_asset_ids'] 
        del data['additional_asset_ids']
        
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SuggestionAlbum:
        """Create SuggestionAlbum from database row data."""
        # Make a copy to avoid modifying the original
        data = dict(data)
        
        # Handle datetime fields
        for date_field in ['created_at', 'event_start_date', 'event_end_date']:
            if data.get(date_field) and isinstance(data[date_field], str):
                try:
                    data[date_field] = datetime.fromisoformat(data[date_field].replace('Z', '+00:00'))
                except ValueError:
                    try:
                        data[date_field] = datetime.strptime(data[date_field], '%Y-%m-%d %H:%M:%S.%f')
                    except ValueError:
                        logger.warning(f"Could not parse date field {date_field}: {data[date_field]}")
                        data[date_field] = None
        
        # Convert JSON strings to lists
        for field, json_field in [
            ('strong_asset_ids', 'strong_asset_ids_json'),
            ('weak_asset_ids', 'weak_asset_ids_json'), 
            ('additional_asset_ids', 'additional_asset_ids_json')
        ]:
            if json_field in data:
                try:
                    data[field] = json.loads(data[json_field] or '[]')
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"Could not parse JSON field {json_field}: {data[json_field]}")
                    data[field] = []
                # Remove the JSON version
                del data[json_field]
            else:
                data[field] = []
        
        # Only include fields that exist in the dataclass
        filtered_data = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        
        return cls(**filtered_data)
    
    @classmethod
    def from_clustering_candidate(cls, candidate: Union['ClusteringCandidate', Dict[str, Any]], location: Optional[str]) -> SuggestionAlbum:
        """Create SuggestionAlbum from clustering algorithm output."""
        if hasattr(candidate, 'strong_asset_ids'):
            # It's a ClusteringCandidate DTO
            return cls(
                status='pending_enrichment',
                created_at=datetime.now(),
                event_start_date=candidate.min_date,
                event_end_date=candidate.max_date or candidate.min_date,
                location=location,
                strong_asset_ids=candidate.strong_asset_ids,
                weak_asset_ids=candidate.weak_asset_ids,
                cover_asset_id=candidate.strong_asset_ids[0] if candidate.strong_asset_ids else None
            )
        else:
            # It's a dictionary (legacy)
            return cls(
                status='pending_enrichment',
                created_at=datetime.now(),
                event_start_date=candidate['min_date'].to_pydatetime() if hasattr(candidate.get('min_date'), 'to_pydatetime') else candidate.get('min_date'),
                event_end_date=candidate.get('max_date', candidate.get('min_date')),
                location=location,
                strong_asset_ids=candidate.get('strong_asset_ids', []),
                weak_asset_ids=candidate.get('weak_asset_ids', []),
                cover_asset_id=candidate.get('strong_asset_ids', [None])[0] if candidate.get('strong_asset_ids') else None
            )

@dataclass  
class ImmichAlbum:
    """Represents an album from the Immich API."""
    album_id: AlbumId
    title: str
    description: Optional[str] = None
    asset_ids: List[AssetId] = field(default_factory=list)
    asset_count: int = 0
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    location: Optional[str] = None
    cover_asset_id: Optional[AssetId] = None
    additional_asset_ids: List[AssetId] = field(default_factory=list)
    
    @property
    def has_additions(self) -> bool:
        """Check if there are additional assets that can be added."""
        return len(self.additional_asset_ids) > 0
    
    @property
    def total_asset_count(self) -> int:
        """Get total count including potential additions."""
        return len(self.asset_ids) + len(self.additional_asset_ids)
    
    def to_suggestion_album(self) -> SuggestionAlbum:
        """Convert to a SuggestionAlbum for unified handling."""
        return SuggestionAlbum(
            status='from_immich',
            created_at=datetime.now(),
            event_start_date=self.start_date,
            event_end_date=self.end_date,
            location=self.location,
            vlm_title=self.title,
            vlm_description=self.description,
            strong_asset_ids=self.asset_ids,
            weak_asset_ids=[],  # Existing albums don't have weak assets
            cover_asset_id=self.cover_asset_id,
            immich_album_id=self.album_id,
            additional_asset_ids=self.additional_asset_ids
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ImmichAlbum:
        """Create ImmichAlbum from dictionary data."""
        # Handle datetime fields
        for date_field in ['start_date', 'end_date']:
            if data.get(date_field) and isinstance(data[date_field], str):
                try:
                    data[date_field] = datetime.fromisoformat(data[date_field].replace('Z', '+00:00'))
                except ValueError:
                    logger.warning(f"Could not parse date field {date_field}: {data[date_field]}")
                    data[date_field] = None
        
        # Only include fields that exist in the dataclass
        filtered_data = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        
        return cls(**filtered_data)

@dataclass
class VLMAnalysis:
    """Represents the output from Vision Language Model analysis."""
    vlm_title: Optional[str] = None
    vlm_description: Optional[str] = None
    cover_asset_id: Optional[AssetId] = None
    confidence_score: Optional[float] = None
    processing_time_seconds: Optional[float] = None
    error_message: Optional[str] = None
    
    @property
    def is_successful(self) -> bool:
        """Check if the analysis was successful."""
        return self.error_message is None and self.vlm_title is not None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VLMAnalysis:
        """Create VLMAnalysis from dictionary data."""
        filtered_data = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered_data)

@dataclass
class ClusteringCandidate:
    """Represents a candidate album found by clustering."""
    strong_asset_ids: List[AssetId]
    weak_asset_ids: List[AssetId] = field(default_factory=list)
    min_date: Optional[datetime] = None
    max_date: Optional[datetime] = None
    primary_location: Optional[str] = None
    confidence_score: Optional[float] = None
    gps_coords: List[tuple[float, float]] = field(default_factory=list)
    
    @property
    def all_asset_ids(self) -> List[AssetId]:
        """Get all asset IDs."""
        return self.strong_asset_ids + self.weak_asset_ids
    
    @property
    def asset_count(self) -> int:
        """Get total asset count."""
        return len(self.strong_asset_ids) + len(self.weak_asset_ids)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ClusteringCandidate:
        """Create ClusteringCandidate from dictionary data."""
        # Handle datetime fields
        for date_field in ['min_date', 'max_date']:
            if data.get(date_field) and isinstance(data[date_field], str):
                try:
                    data[date_field] = datetime.fromisoformat(data[date_field].replace('Z', '+00:00'))
                except ValueError:
                    logger.warning(f"Could not parse date field {date_field}: {data[date_field]}")
                    data[date_field] = None
        
        filtered_data = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered_data)

# Utility functions for conversion
def suggestion_from_db_row(row: Union[Dict[str, Any], Any]) -> SuggestionAlbum:
    """Convert database row to SuggestionAlbum DTO."""
    if hasattr(row, 'keys'):
        # sqlite3.Row or dict-like object
        return SuggestionAlbum.from_dict(dict(row))
    else:
        # Regular dictionary
        return SuggestionAlbum.from_dict(row)

def photo_from_db_row(row: Union[Dict[str, Any], Any]) -> PhotoAsset:
    """Convert database row to PhotoAsset DTO."""
    if hasattr(row, 'keys'):
        # sqlite3.Row or dict-like object  
        return PhotoAsset.from_dict(dict(row))
    else:
        # Regular dictionary
        return PhotoAsset.from_dict(row)

def album_from_api_response(data: Dict[str, Any]) -> ImmichAlbum:
    """Convert Immich API response to ImmichAlbum DTO."""
    return ImmichAlbum.from_dict(data)