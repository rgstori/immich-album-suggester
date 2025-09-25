# app/ui_utils.py
"""
UI utility functions for consistent rendering and formatting throughout the Streamlit interface.

This module consolidates repetitive UI patterns identified during the DTO cleanup phase,
providing reusable functions for thumbnail rendering, date formatting, metadata display,
and layout management.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Tuple
import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from .models import SuggestionAlbum
from .utils import parse_datetime_safe


def render_thumbnail_card(
    asset_id: str, 
    get_cached_thumbnail_func,
    get_photo_metadata_func = None,
    show_metadata: bool = True,
    cover_selection_mode: bool = False,
    current_cover_id: str = None,
    container = None,
    button_prefix: str = "",
    thumbnail_caption: str = ""
) -> tuple[bool, str]:
    """
    Unified thumbnail rendering with consistent error handling and behavior.
    
    Args:
        asset_id: The asset ID to render thumbnail for
        get_cached_thumbnail_func: Function to get cached thumbnail bytes
        get_photo_metadata_func: Function to get photo date/location metadata
        show_metadata: Whether to display photo metadata below thumbnail
        cover_selection_mode: Whether to show cover selection button
        current_cover_id: ID of current cover photo (for comparison)
        container: Optional streamlit container to render in
        button_prefix: Prefix for button keys to avoid conflicts
        thumbnail_caption: Optional caption to show on image
        
    Returns:
        Tuple of (action_taken, action_type) where action_type is 'view', 'cover', or 'none'
    """
    if container is None:
        container = st
    
    try:
        thumb_bytes = get_cached_thumbnail_func(asset_id)
        
        if thumb_bytes:
            try:
                # Display the image with caption if provided
                container.image(
                    thumb_bytes, 
                    caption=thumbnail_caption,
                    use_container_width=True
                )
                
                # Show metadata if requested and function provided
                if show_metadata and get_photo_metadata_func:
                    date_str, location_str = get_photo_metadata_func(asset_id)
                    container.caption(f"📅 {date_str}")
                    container.caption(f"📍 {location_str}")
                
                # Button behavior depends on cover selection mode
                if cover_selection_mode:
                    # In cover selection mode, clicking selects as cover
                    is_current_cover = asset_id == current_cover_id
                    button_text = "✅ Current Cover" if is_current_cover else "🖼️ Set as Cover"
                    button_disabled = is_current_cover
                    button_type = "secondary" if is_current_cover else "primary"
                    
                    if container.button(
                        button_text, 
                        key=f"{button_prefix}cover_{asset_id}",
                        help="Set as album cover",
                        use_container_width=True,
                        disabled=button_disabled,
                        type=button_type
                    ):
                        return True, 'cover'
                else:
                    # Normal mode - view photo
                    if container.button(
                        "👁️", 
                        key=f"{button_prefix}view_{asset_id}",
                        help="View full photo",
                        use_container_width=True
                    ):
                        return True, 'view'
                
                return True, 'none'
                
            except Exception as e:
                # If thumbnail display fails, show error with asset info
                container.error("⚠️ Corrupted thumbnail")
                container.caption(f"Asset: {asset_id[:8]}...")
                
                # Still allow interaction (viewing or cover selection)
                if cover_selection_mode:
                    is_current_cover = asset_id == current_cover_id
                    button_text = "✅ Current Cover" if is_current_cover else "🖼️ Set as Cover"
                    button_disabled = is_current_cover
                    
                    if container.button(
                        button_text,
                        key=f"{button_prefix}cover_{asset_id}",
                        help="Set as album cover",
                        use_container_width=True,
                        disabled=button_disabled
                    ):
                        return True, 'cover'
                else:
                    if container.button(
                        "👁️ Try anyway",
                        key=f"{button_prefix}view_{asset_id}",
                        help="Try to view full photo",
                        use_container_width=True
                    ):
                        return True, 'view'
                
                return False, 'none'
        else:
            container.error("🖼️", help=f"Failed to load thumbnail for asset {asset_id}")
            
            # Still allow interaction (viewing or cover selection)
            if cover_selection_mode:
                is_current_cover = asset_id == current_cover_id
                button_text = "✅ Current Cover" if is_current_cover else "🖼️ Set as Cover"
                button_disabled = is_current_cover
                
                if container.button(
                    button_text,
                    key=f"{button_prefix}cover_{asset_id}",
                    help="Set as album cover",
                    use_container_width=True,
                    disabled=button_disabled
                ):
                    return True, 'cover'
            else:
                if container.button(
                    "👁️ Try anyway",
                    key=f"{button_prefix}view_{asset_id}",
                    help="Try to view full photo",
                    use_container_width=True
                ):
                    return True, 'view'
            
            return False, 'none'
            
    except Exception as e:
        container.error(f"Error loading thumbnail: {str(e)}")
        return False, 'none'


def format_date_range(start_date: Optional[datetime], end_date: Optional[datetime] = None) -> str:
    """
    Centralized date range formatting with consistent logic.
    
    Args:
        start_date: The start date of the range
        end_date: Optional end date of the range
        
    Returns:
        Formatted date string (e.g., "15-08-25" or "15-08-25 - 16-08-25")
    """
    if not start_date:
        return "Unknown date"
    
    # Handle string dates that might come from database
    if isinstance(start_date, str):
        start_date = parse_datetime_safe(start_date)
        if not start_date:
            return "Invalid date"
    
    if isinstance(end_date, str):
        end_date = parse_datetime_safe(end_date)
    
    start_formatted = start_date.strftime('%d-%m-%y')
    
    # Only show end date if it's different from start date
    if end_date and start_date.date() != end_date.date():
        end_formatted = end_date.strftime('%d-%m-%y')
        return f"{start_formatted} - {end_formatted}"
    else:
        return start_formatted


def format_suggestion_metadata(suggestion: SuggestionAlbum, include_counts: bool = True) -> str:
    """
    Create standardized metadata string for suggestion display.
    
    Args:
        suggestion: SuggestionAlbum DTO
        include_counts: Whether to include photo counts in metadata
        
    Returns:
        Formatted metadata string with photo counts, dates, and location
    """
    info_parts = []
    
    # Photo counts
    if include_counts:
        if suggestion.status == 'from_immich':
            core_count = len(suggestion.strong_asset_ids)
            additional_count = len(suggestion.additional_asset_ids)
            if additional_count > 0:
                photo_text = f"{core_count} (+{additional_count}) photos"
            else:
                photo_text = f"{core_count} photos"
        else:
            core_count = len(suggestion.strong_asset_ids)
            additional_count = len(suggestion.weak_asset_ids)
            if additional_count > 0:
                photo_text = f"{core_count} (+{additional_count}) photos"
            else:
                photo_text = f"{core_count} photos"
        
        info_parts.append(photo_text)
    
    # Date range
    date_text = format_date_range(suggestion.event_start_date, suggestion.event_end_date)
    if date_text != "Unknown date":
        info_parts.append(date_text)
    
    # Location
    if suggestion.location:
        info_parts.append(suggestion.location)
    
    return " | ".join(info_parts)


def format_photo_info(photo_metadata: dict) -> str:
    """
    Format individual photo metadata for display.
    
    Args:
        photo_metadata: Dictionary containing photo metadata
        
    Returns:
        Formatted string with date and location info
    """
    info_parts = []
    
    # Date
    date_taken = photo_metadata.get('dateTimeOriginal') or photo_metadata.get('fileCreatedAt')
    if date_taken:
        if isinstance(date_taken, str):
            date_taken = parse_datetime_safe(date_taken)
        if date_taken:
            info_parts.append(date_taken.strftime('%d-%m-%y %H:%M'))
    
    # Location
    location_parts = []
    for field in ['city', 'state', 'country']:
        if photo_metadata.get(field):
            location_parts.append(photo_metadata[field])
    
    if location_parts:
        info_parts.append(', '.join(location_parts))
    
    return " | ".join(info_parts) if info_parts else "No metadata"


def render_pagination_controls(
    items: list,
    items_per_page: int,
    page_key: str,
    ui_state_obj,
    show_jump_button: bool = False,
    jump_button_config: dict = None
) -> tuple[list, bool]:
    """
    Generalized pagination controls with configurable page keys.
    
    Args:
        items: List of items to paginate
        items_per_page: Number of items per page
        page_key: Key for storing current page in UI state
        ui_state_obj: UI state object with page tracking
        show_jump_button: Whether to show a special jump button
        jump_button_config: Config dict with 'text', 'target_page', 'condition' keys
        
    Returns:
        Tuple of (items_for_current_page, has_pagination)
    """
    total_pages = (len(items) + items_per_page - 1) // items_per_page
    
    if total_pages <= 1:
        return items, False
    
    # Get current page from UI state
    current_page = getattr(ui_state_obj, page_key, 0)
    
    # Pagination controls
    col1, col2, col3, col4 = st.columns([1, 1, 2, 1])
    
    with col1:
        if st.button("◀ Previous", key=f"{page_key}_prev", disabled=current_page == 0):
            setattr(ui_state_obj, page_key, current_page - 1)
            st.rerun()
    
    with col2:
        if st.button("Next ▶", key=f"{page_key}_next", disabled=current_page == total_pages - 1):
            setattr(ui_state_obj, page_key, current_page + 1)
            st.rerun()
    
    with col3:
        st.caption(f"Page {current_page + 1} of {total_pages} • {len(items)} items")
    
    with col4:
        # Optional jump button (e.g., "Go to cover photo")
        if show_jump_button and jump_button_config:
            if jump_button_config.get('condition', True):  # Default to True if no condition
                if st.button(
                    jump_button_config['text'], 
                    key=f"{page_key}_jump", 
                    help=jump_button_config.get('help', '')
                ):
                    setattr(ui_state_obj, page_key, jump_button_config['target_page'])
                    st.rerun()
    
    # Get items for current page
    start_idx = current_page * items_per_page
    end_idx = min(start_idx + items_per_page, len(items))
    page_items = items[start_idx:end_idx]
    
    st.caption(f"Showing items {start_idx + 1}-{end_idx}")
    
    return page_items, True


class UILayoutManager:
    """
    Centralized UI layout management for consistent spacing and responsive design.
    """
    
    @staticmethod
    def action_buttons(num_buttons: int = 2) -> List[DeltaGenerator]:
        """
        Create standard action button layout.
        
        Args:
            num_buttons: Number of buttons to create columns for
            
        Returns:
            List of streamlit column objects
        """
        if num_buttons == 2:
            return st.columns(2)
        elif num_buttons == 3:
            return st.columns(3)
        elif num_buttons == 4:
            return st.columns(4)
        else:
            return st.columns([1] * num_buttons)
    
    @staticmethod
    def pagination_controls() -> Tuple[DeltaGenerator, DeltaGenerator, DeltaGenerator]:
        """
        Create standard pagination layout with prev/info/next structure.
        
        Returns:
            Tuple of (prev_col, info_col, next_col)
        """
        return st.columns([1, 2, 1])
    
    @staticmethod
    def photo_grid_layout(items_per_row: int = 4) -> List[DeltaGenerator]:
        """
        Create responsive photo grid layout.
        
        Args:
            items_per_row: Number of photos per row
            
        Returns:
            List of column objects for the grid
        """
        return st.columns(items_per_row)
    
    @staticmethod
    def sidebar_card_layout() -> Tuple[DeltaGenerator, DeltaGenerator]:
        """
        Create standard sidebar card layout with thumbnail and content.
        
        Returns:
            Tuple of (thumbnail_col, content_col)
        """
        return st.columns([1, 2])
    
    @staticmethod
    def metadata_layout() -> Tuple[DeltaGenerator, DeltaGenerator]:
        """
        Create layout for metadata display with label and value columns.
        
        Returns:
            Tuple of (label_col, value_col)
        """
        return st.columns([1, 3])


def _render_photo_metadata_caption(photo_metadata: dict, container) -> None:
    """
    Internal helper to render photo metadata as caption.
    
    Args:
        photo_metadata: Dictionary containing photo metadata
        container: Streamlit container to render in
    """
    metadata_text = format_photo_info(photo_metadata)
    container.caption(metadata_text)


