# app/ui_state.py
"""
Session state management for the Streamlit UI.

This module provides a centralized, type-safe way to manage all UI state,
replacing the scattered st.session_state usage throughout ui.py.
"""
from __future__ import annotations
from typing import Optional, Set, Literal, Dict, Any
import streamlit as st
import logging

logger = logging.getLogger(__name__)

ViewMode = Literal["album", "photo"]
SortBy = Literal["image_count", "event_start_date"]
SortOrder = Literal["asc", "desc"]


class UISessionState:
    """
    Centralized session state management for the Streamlit UI.
    
    This class provides a clean interface for managing UI state with proper
    type safety, validation, and clear state transitions. It encapsulates
    all session state variables and their interdependencies.
    """
    
    def __init__(self):
        """Initialize the session state manager with default values."""
        self._init_defaults()
    
    def _init_defaults(self) -> None:
        """Set default values for all session state variables."""
        # Core navigation state
        st.session_state.setdefault("selected_suggestion_id", None)
        st.session_state.setdefault("selected_asset_id", None)
        st.session_state.setdefault("view_mode", "album")
        
        # Pagination state
        st.session_state.setdefault("gallery_page", 0)
        st.session_state.setdefault("core_photos_page", 0)
        st.session_state.setdefault("weak_assets_page", 0)
        
        # Selection state
        st.session_state.setdefault("included_weak_assets", set())
        st.session_state.setdefault("suggestions_to_enrich", set())
        
        # Table sorting state
        st.session_state.setdefault("sort_by", "image_count")
        st.session_state.setdefault("sort_order", "desc")
        
        # Confirmation dialog state
        st.session_state.setdefault("confirm_delete_all", False)
        st.session_state.setdefault("confirm_delete_all_table", False)
        st.session_state.setdefault("select_all_weak", False)
        
        # Cover selection state
        st.session_state.setdefault("cover_selection_mode", False)
    
    # --- Core Navigation Properties ---
    
    @property
    def selected_suggestion_id(self) -> Optional[int]:
        """Get the currently selected suggestion ID."""
        return st.session_state.get("selected_suggestion_id", None)
    
    @selected_suggestion_id.setter
    def selected_suggestion_id(self, value: Optional[int]) -> None:
        """Set the selected suggestion ID and reset related state."""
        if st.session_state.selected_suggestion_id != value:
            # Reset pagination when switching albums
            self.reset_pagination()
            # Clear weak asset selections
            self.clear_weak_asset_selections()
        st.session_state.selected_suggestion_id = value
        st.session_state.view_mode = "album"
    
    @property
    def selected_asset_id(self) -> Optional[str]:
        """Get the currently selected asset ID."""
        return st.session_state.get("selected_asset_id", None)
    
    @selected_asset_id.setter
    def selected_asset_id(self, value: Optional[str]) -> None:
        """Set the selected asset ID."""
        st.session_state.selected_asset_id = value
    
    @property
    def view_mode(self) -> ViewMode:
        """Get the current view mode."""
        return st.session_state.get("view_mode", "album")
    
    @view_mode.setter
    def view_mode(self, value: ViewMode) -> None:
        """Set the view mode."""
        st.session_state.view_mode = value
    
    # --- Pagination Properties ---
    
    @property
    def gallery_page(self) -> int:
        """Get the current gallery page."""
        return st.session_state.get("gallery_page", 0)
    
    @gallery_page.setter
    def gallery_page(self, value: int) -> None:
        """Set the gallery page."""
        st.session_state.gallery_page = max(0, value)
    
    @property
    def core_photos_page(self) -> int:
        """Get the current core photos page."""
        return st.session_state.get("core_photos_page", 0)
    
    @core_photos_page.setter
    def core_photos_page(self, value: int) -> None:
        """Set the core photos page."""
        st.session_state.core_photos_page = max(0, value)
    
    @property
    def weak_assets_page(self) -> int:
        """Get the current weak assets page."""
        return st.session_state.get("weak_assets_page", 0)
    
    @weak_assets_page.setter
    def weak_assets_page(self, value: int) -> None:
        """Set the weak assets page."""
        st.session_state.weak_assets_page = max(0, value)
    
    # --- Selection Properties ---
    
    @property
    def included_weak_assets(self) -> Set[str]:
        """Get the set of included weak assets."""
        return st.session_state.get("included_weak_assets", set())
    
    @property
    def suggestions_to_enrich(self) -> Set[int]:
        """Get the set of suggestions selected for enrichment."""
        return st.session_state.get("suggestions_to_enrich", set())
    
    # --- Sorting Properties ---
    
    @property
    def sort_by(self) -> SortBy:
        """Get the current sort field."""
        return st.session_state.get("sort_by", "image_count")
    
    @sort_by.setter
    def sort_by(self, value: SortBy) -> None:
        """Set the sort field."""
        st.session_state.sort_by = value
    
    @property
    def sort_order(self) -> SortOrder:
        """Get the current sort order."""
        return st.session_state.get("sort_order", "desc")
    
    @sort_order.setter
    def sort_order(self, value: SortOrder) -> None:
        """Set the sort order."""
        st.session_state.sort_order = value
    
    # --- State Transition Methods ---
    
    def switch_to_album(self, suggestion_id: int) -> None:
        """
        Switch to viewing a specific album.
        
        This is the primary state transition method that handles all
        necessary cleanup and initialization when switching albums.
        """
        logger.debug(f"Switching to album {suggestion_id}")
        self.selected_suggestion_id = suggestion_id
        # selected_suggestion_id setter handles pagination reset and weak asset clearing
    
    def switch_to_photo(self, asset_id: str) -> None:
        """Switch to viewing a specific photo."""
        logger.debug(f"Switching to photo {asset_id}")
        self.selected_asset_id = asset_id
        self.view_mode = "photo"
    
    def switch_to_table_view(self) -> None:
        """Switch back to the table view."""
        logger.debug("Switching to table view")
        self.selected_suggestion_id = None
        self.selected_asset_id = None
        self.view_mode = "album"
    
    def return_to_album_from_photo(self) -> None:
        """Return to album view from photo view."""
        logger.debug("Returning to album view from photo")
        self.selected_asset_id = None
        self.view_mode = "album"
    
    # --- Selection Management Methods ---
    
    def toggle_suggestion_selection(self, suggestion_id: int) -> None:
        """Toggle selection of a suggestion for bulk operations."""
        if suggestion_id in self.suggestions_to_enrich:
            self.suggestions_to_enrich.remove(suggestion_id)
        else:
            self.suggestions_to_enrich.add(suggestion_id)
    
    def toggle_weak_asset_selection(self, asset_id: str) -> None:
        """Toggle selection of a weak asset for inclusion."""
        if asset_id in self.included_weak_assets:
            self.included_weak_assets.discard(asset_id)
        else:
            self.included_weak_assets.add(asset_id)
        
        # Update corresponding checkbox state
        checkbox_key = f"cb_weak_{asset_id}"
        st.session_state[checkbox_key] = asset_id in self.included_weak_assets
    
    def select_all_weak_assets(self, asset_ids: list[str]) -> None:
        """Select all weak assets."""
        self.included_weak_assets.update(asset_ids)
        # Update all checkbox states
        for asset_id in asset_ids:
            st.session_state[f"cb_weak_{asset_id}"] = True
        st.session_state.select_all_weak = True
    
    def deselect_all_weak_assets(self, asset_ids: list[str]) -> None:
        """Deselect all weak assets."""
        self.included_weak_assets.clear()
        # Update all checkbox states
        for asset_id in asset_ids:
            st.session_state[f"cb_weak_{asset_id}"] = False
        st.session_state.select_all_weak = False
    
    def clear_suggestion_selections(self) -> None:
        """Clear all suggestion selections."""
        self.suggestions_to_enrich.clear()
    
    def clear_weak_asset_selections(self) -> None:
        """Clear all weak asset selections."""
        self.included_weak_assets.clear()
    
    # --- Cover Selection Properties ---
    
    @property
    def cover_selection_mode(self) -> bool:
        """Get the cover selection mode state."""
        return st.session_state.get("cover_selection_mode", False)
    
    @cover_selection_mode.setter
    def cover_selection_mode(self, value: bool) -> None:
        """Set the cover selection mode state."""
        st.session_state.cover_selection_mode = value
    
    def enable_cover_selection_mode(self) -> None:
        """Enable cover selection mode."""
        self.cover_selection_mode = True
        logger.debug("Cover selection mode enabled")
    
    def disable_cover_selection_mode(self) -> None:
        """Disable cover selection mode."""
        self.cover_selection_mode = False
        logger.debug("Cover selection mode disabled")
    
    # --- Pagination Management Methods ---
    
    def reset_pagination(self) -> None:
        """Reset all pagination to the first page."""
        self.gallery_page = 0
        self.core_photos_page = 0
        self.weak_assets_page = 0
    
    def next_core_page(self, max_pages: int) -> None:
        """Go to next core photos page if possible."""
        if self.core_photos_page < max_pages - 1:
            self.core_photos_page += 1
    
    def prev_core_page(self) -> None:
        """Go to previous core photos page if possible."""
        if self.core_photos_page > 0:
            self.core_photos_page -= 1
    
    def next_weak_page(self, max_pages: int) -> None:
        """Go to next weak assets page if possible."""
        if self.weak_assets_page < max_pages - 1:
            self.weak_assets_page += 1
    
    def prev_weak_page(self) -> None:
        """Go to previous weak assets page if possible."""
        if self.weak_assets_page > 0:
            self.weak_assets_page -= 1
    
    def set_cover_page(self, cover_page: int) -> None:
        """Set the core photos page to show the cover photo."""
        self.core_photos_page = cover_page
    
    # --- Sorting Management Methods ---
    
    def toggle_sort(self, field: SortBy) -> None:
        """Toggle sorting by the specified field."""
        if self.sort_by == field:
            # Toggle order if same field
            self.sort_order = "asc" if self.sort_order == "desc" else "desc"
        else:
            # Switch to new field with descending order
            self.sort_by = field
            self.sort_order = "desc"
        logger.debug(f"Sort changed to {self.sort_by} {self.sort_order}")
    
    def update_sorting(self, sort_by: SortBy, sort_order: SortOrder) -> None:
        """Update sorting if values have changed."""
        if self.sort_by != sort_by or self.sort_order != sort_order:
            self.sort_by = sort_by
            self.sort_order = sort_order
            logger.debug(f"Sort updated to {sort_by} {sort_order}")
    
    # --- Confirmation Dialog Management ---
    
    def get_confirmation_state(self, key: str) -> bool:
        """Get the state of a confirmation dialog."""
        return st.session_state.get(key, False)
    
    def set_confirmation_state(self, key: str, value: bool) -> None:
        """Set the state of a confirmation dialog."""
        st.session_state[key] = value
    
    def clear_confirmation_state(self, key: str) -> None:
        """Clear a confirmation dialog state."""
        if key in st.session_state:
            del st.session_state[key]
    
    def get_merge_confirmation_key(self, suggestion_ids: list[int]) -> str:
        """Generate a unique confirmation key for merge operations."""
        return f"confirm_merge_{'_'.join(map(str, sorted(suggestion_ids)))}"
    
    # --- Merge Intent Management ---
    
    def set_merge_intent(self, suggestion_ids: list[int]) -> None:
        """Set the merge intent for processing."""
        st.session_state.merge_intent = suggestion_ids
        logger.debug(f"Merge intent set for suggestions: {suggestion_ids}")
    
    def get_merge_intent(self) -> Optional[list[int]]:
        """Get the current merge intent."""
        return getattr(st.session_state, 'merge_intent', None)
    
    def clear_merge_intent(self) -> None:
        """Clear the merge intent."""
        if hasattr(st.session_state, 'merge_intent'):
            del st.session_state.merge_intent
    
    def has_merge_intent(self) -> bool:
        """Check if there is a pending merge intent."""
        return hasattr(st.session_state, 'merge_intent') and st.session_state.merge_intent
    
    # --- Utility Methods ---
    
    def get_session_info(self) -> Dict[str, Any]:
        """Get a summary of current session state for debugging."""
        return {
            "selected_suggestion_id": self.selected_suggestion_id,
            "selected_asset_id": self.selected_asset_id,
            "view_mode": self.view_mode,
            "pagination": {
                "gallery_page": self.gallery_page,
                "core_photos_page": self.core_photos_page,
                "weak_assets_page": self.weak_assets_page,
            },
            "selections": {
                "suggestions_to_enrich": len(self.suggestions_to_enrich),
                "included_weak_assets": len(self.included_weak_assets),
            },
            "sorting": {
                "sort_by": self.sort_by,
                "sort_order": self.sort_order,
            }
        }


# Global instance to be used throughout the UI
ui_state = UISessionState()