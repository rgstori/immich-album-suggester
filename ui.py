# ui.py
"""
The main user interface for the Immich Album Suggester application.

This UI serves as the central command console for the application. It is built
with Streamlit and is designed to be a pure presentation layer. All business
logic, data access, and process management are delegated to the services in
the `app.services` package.

Key Responsibilities:
- Rendering the list of pending album suggestions.
- Triggering background processes for clustering and enrichment via the ProcessService.
- Displaying a detailed, editable view for a selected album suggestion.
- Handling user actions like approving or rejecting suggestions.
"""

# The config_service MUST be the very first app import to ensure logging is set up.
try:
    from app.services import config
except ImportError:
    import sys
    print("FATAL: Could not import services. Please run this script as a module: `streamlit run ui.py` from the project root.", file=sys.stderr)
    sys.exit(1)

import streamlit as st
import logging
import math
import time
from PIL import Image, ImageOps
from io import BytesIO

# Import the services that will handle all the heavy lifting.
from app.services import db_service, immich_service, process_service
# Using an alias for our exception base class for cleaner code.
from app.exceptions import AppServiceError
# Import the centralized session state manager
from app.ui_state import ui_state
# Import DTOs for type-safe data handling
from app.models import SuggestionAlbum
# Import UI utilities for consistent rendering
from app.ui_utils import format_date_range, format_suggestion_metadata, UILayoutManager, render_thumbnail_card, render_pagination_controls

# Initialize the logger for this UI module.
logger = logging.getLogger(__name__)


# --- Section 1: UI State and Cache Management ---

def init_session_state():
    """
    Initializes all necessary keys in Streamlit's session state.
    This is now handled by the UISessionState class for better organization.
    """
    # Explicitly call initialization to ensure session state is properly set up
    ui_state._init_defaults()

@st.cache_resource
def get_image_cache():
    """
    Returns a singleton instance of an LRU cache for image thumbnails.
    Using `st.cache_resource` ensures the cache object persists across reruns
    and is not re-created, preserving cached images for a smooth UX.
    The cache has a fixed size to prevent unbounded memory growth.
    """
    class ImageLRUCache:
        # A simple LRU Cache implementation could be used here.
        # For simplicity, we'll use Streamlit's built-in caching per-image.
        # A more complex, size-limited cache would be the next step for optimization.
        pass
    # For now, we'll rely on st.cache_data for individual images.
    # A true LRU object would be implemented here if needed.
    return {} # Placeholder

@st.cache_data(max_entries=config.get('ui.cache_max_entries', 500), ttl="1h", show_spinner=False)
def get_cached_thumbnail(asset_id: str) -> bytes | None:
    """
    A cached function to fetch and store a single thumbnail.
    Streamlit's caching decorators handle the logic of checking if the
    data for a given `asset_id` is already in memory.
    """
    if not asset_id:
        return None
    try:
        image_bytes = immich_service.get_thumbnail_bytes(asset_id)
        if image_bytes:
            # Correct image orientation before caching and displaying.
            # This is a critical UX fix for mobile photos.
            corrected_bytes = _correct_image_orientation(image_bytes)
            if corrected_bytes:
                return corrected_bytes
            else:
                # If orientation correction failed, return original bytes
                # The UI will handle display errors gracefully
                logger.warning(f"Using original bytes for asset {asset_id} due to processing failure")
                return image_bytes
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch thumbnail for asset {asset_id} for caching: {e}")
        return None

def _correct_image_orientation(image_bytes: bytes) -> bytes:
    """Reads EXIF data from image bytes and applies necessary rotation."""
    try:
        image = Image.open(BytesIO(image_bytes))
        # First verify the image is valid
        image.verify()
        
        # Re-open the image for processing (verify() consumes the image)
        image = Image.open(BytesIO(image_bytes))
        
        # This function handles the complex logic of interpreting EXIF orientation tags.
        transposed_image = ImageOps.exif_transpose(image)
        buf = BytesIO()
        # Save back to a new buffer in a standard format.
        transposed_image.convert("RGB").save(buf, format='JPEG')
        return buf.getvalue()
    except Exception as e:
        # If EXIF parsing or image processing fails, log and return None
        logger.warning(f"Failed to process image orientation: {e}")
        return None

@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_photo_metadata(asset_id: str) -> tuple[str, str]:
    """
    Get formatted date and location for a photo.
    Returns tuple of (date_str, location_str) for display.
    """
    try:
        exif_data = immich_service.get_exif_data(asset_id)
        if not exif_data:
            return "No date", "No location"
        
        # Format date - try multiple date fields and formats
        date_str = "No date"
        date_candidates = [
            exif_data.get('dateTimeOriginal'),
            exif_data.get('dateTime'),
            exif_data.get('createDate'),
            exif_data.get('modifyDate'),
            exif_data.get('fileCreatedAt'),
            exif_data.get('createdAt')
        ]
        
        from datetime import datetime
        for date_candidate in date_candidates:
            if date_candidate:
                try:
                    # Handle different date formats
                    if isinstance(date_candidate, str):
                        # Try ISO format first
                        if 'T' in date_candidate:
                            dt = datetime.fromisoformat(date_candidate.replace('Z', '+00:00'))
                        # Try simple YYYY-MM-DD format
                        elif '-' in date_candidate and len(date_candidate) >= 10:
                            dt = datetime.strptime(date_candidate[:10], '%Y-%m-%d')
                        # Try YYYY:MM:DD format (common in EXIF)
                        elif ':' in date_candidate and len(date_candidate) >= 10:
                            dt = datetime.strptime(date_candidate[:10], '%Y:%m:%d')
                        else:
                            continue
                    elif hasattr(date_candidate, 'year'):  # datetime object
                        dt = date_candidate
                    else:
                        continue
                    
                    date_str = dt.strftime("%b %d, %Y")
                    break  # Success, stop trying
                    
                except (ValueError, TypeError, AttributeError):
                    continue  # Try next date candidate
        
        # Format location
        location_str = "No location"
        city = exif_data.get('city', '')
        state = exif_data.get('state', '')
        country = exif_data.get('country', '')
        
        if city or state or country:
            location_parts = [part for part in [city, state, country] if part]
            if len(location_parts) >= 2:
                location_str = f"{location_parts[0]}, {location_parts[-1]}"  # City, Country or State, Country
            elif len(location_parts) == 1:
                location_str = location_parts[0]
        
        return date_str, location_str
        
    except Exception as e:
        logger.warning(f"Failed to get metadata for asset {asset_id}: {e}")
        return "No date", "No location"

def switch_to_album_view(suggestion_id: int):
    """
    Callback to cleanly switch the main view to a specific album.
    Uses the centralized session state manager for clean state transitions.
    """
    ui_state.switch_to_album(suggestion_id)
    
    # We don't need to manually clear caches here, as Streamlit's data flow
    # will naturally call the correct cached functions with the new ID.
    st.rerun()

# --- Section 2: UI Component Rendering ---

def render_scan_controls():
    """Renders UI for starting scans and monitoring their progress."""
    st.sidebar.subheader("Scan Controls")

    # Check the status of the main scan process.
    is_scan_running = process_service.is_running('scan')
    
    col1, col2 = st.sidebar.columns(2)
    
    if col1.button("Incremental Scan", use_container_width=True, disabled=is_scan_running):
        try:
            process_service.start_scan('incremental')
            st.toast("🚀 Incremental scan started!", icon="🚀")
            st.rerun()
        except AppServiceError as e:
            st.error(f"Failed to start scan: {e}")

    if col2.button("Full Rescan", use_container_width=True, type="primary", disabled=is_scan_running):
        try:
            process_service.start_scan('full')
            st.toast("🚀 Full rescan started!", icon="🚀")
            st.rerun()
        except AppServiceError as e:
            st.error(f"Failed to start scan: {e}")

    # Album cache refresh button
    if st.sidebar.button("🔄 Refresh Album Cache", use_container_width=True, help="Clear cached album data to detect new albums"):
        try:
            immich_service.clear_album_cache()
            st.toast("Album cache cleared!", icon="🔄")
        except Exception as e:
            st.error(f"Failed to clear cache: {e}")

    # Display real-time logs from the database.
    with st.sidebar.expander("Live Logs", expanded=is_scan_running):
        log_container = st.container(height=config.get('ui.log_container_height', 200))
        logs = db_service.get_scan_logs()
        recent_count = config.get('ui.recent_logs_count', 50)
        for log in reversed(logs[-recent_count:]): # Show last N logs
            level = log['level']
            msg = f"[{level}] {log['message']}"
            if "error" in level.lower() or "fatal" in level.lower():
                log_container.error(msg)
            elif "warn" in level.lower():
                log_container.warning(msg)
            else:
                log_container.write(msg)
        if not logs and not is_scan_running:
            log_container.info("Logs will appear here when a scan is running.")


def render_suggestion_list():
    """Renders the list of pending suggestions in the sidebar."""
    st.sidebar.subheader("Pending Suggestions")
    
    # --- Delete All Button ---
    # Check if we should show confirmation
    if 'confirm_delete_all' not in st.session_state:
        st.session_state.confirm_delete_all = False
    
    if not st.session_state.confirm_delete_all:
        if st.sidebar.button("🗑️ Delete All Pending", use_container_width=True, type="secondary"):
            st.session_state.confirm_delete_all = True
            st.rerun()
    else:
        st.sidebar.warning("⚠️ This will delete ALL pending suggestions!")
        col1, col2 = st.sidebar.columns(2)
        
        if col1.button("✅ Confirm", use_container_width=True, type="primary"):
            try:
                deleted_count = db_service.delete_all_pending_suggestions()
                if deleted_count > 0:
                    st.toast(f"Deleted {deleted_count} pending suggestions!", icon="🗑️")
                    # Clear any selected suggestion if it was deleted
                    ui_state.selected_suggestion_id = None
                    ui_state.suggestions_to_enrich.clear()
                else:
                    st.toast("No pending suggestions to delete", icon="ℹ️")
                st.session_state.confirm_delete_all = False
                st.rerun()
            except Exception as e:
                st.error(f"Failed to delete suggestions: {e}")
                st.session_state.confirm_delete_all = False
        
        if col2.button("❌ Cancel", use_container_width=True):
            st.session_state.confirm_delete_all = False
            st.rerun()
    
    st.sidebar.markdown("---")
    
    # --- Sort Controls ---
    st.sidebar.write("**Sort by:**")
    sort_col1, sort_col2 = st.sidebar.columns(2)
    
    sort_options = {
        "image_count": "Photo Count",
        "event_start_date": "Date",
        "created_at": "Created"
    }
    
    with sort_col1:
        sort_by = st.selectbox(
            "Field",
            options=list(sort_options.keys()),
            format_func=lambda x: sort_options[x],
            index=list(sort_options.keys()).index(ui_state.sort_by),
            key="sort_by_select",
            label_visibility="collapsed"
        )
    
    with sort_col2:
        sort_order = st.selectbox(
            "Order", 
            options=["desc", "asc"],
            format_func=lambda x: "High→Low" if x == "desc" else "Low→High",
            index=0 if ui_state.sort_order == "desc" else 1,
            key="sort_order_select",
            label_visibility="collapsed"
        )
    
    # Update session state if changed
    ui_state.update_sorting(sort_by, sort_order)
    
    # Fetch suggestions with sorting
    suggestions = db_service.get_pending_suggestions(sort_by=ui_state.sort_by, sort_order=ui_state.sort_order)

    if not suggestions:
        st.sidebar.info("No pending suggestions. Run a scan!")
        return

    # --- Bulk Action Controls ---
    st.sidebar.markdown("---")
    st.sidebar.write("**Bulk Actions**")
    
    # First row: Enrich and Clear
    col1, col2 = st.sidebar.columns(2)
    if col1.button("✨ Enrich Selected", use_container_width=True, disabled=not ui_state.suggestions_to_enrich):
        for s_id in list(ui_state.suggestions_to_enrich):
            process_service.start_enrichment(s_id)
        ui_state.clear_suggestion_selections()
        st.toast("Enrichment process(es) started!", icon="✨")
        st.rerun()

    if col2.button("Clear Selection", use_container_width=True):
        ui_state.clear_suggestion_selections()
        st.rerun()
    
    # Second row: Merge button
    if st.sidebar.button("🔗 Merge Selected", use_container_width=True, disabled=len(ui_state.suggestions_to_enrich) < 2):
        # Set merge intent instead of calling handler directly
        ui_state.set_merge_intent(list(ui_state.suggestions_to_enrich))
        st.rerun()

    st.sidebar.markdown("---")

    # --- Scrollable Suggestions Container ---
    with st.sidebar.container(height=600, border=False):
        # --- Render Individual Suggestion Cards ---
        for suggestion in suggestions:
            s_id = suggestion.id
            is_enriching = process_service.is_running(f"enrich_{s_id}") or suggestion.status == 'enriching'

            with st.container(border=True):
                # Use cover photo if available, otherwise first strong asset.
                cover_id = suggestion.cover_asset_id
                if not cover_id:
                    cover_id = suggestion.strong_asset_ids[0] if suggestion.strong_asset_ids else None
                
                # Use the unified thumbnail rendering function
                if cover_id:
                    thumb_bytes = get_cached_thumbnail(cover_id)
                    if thumb_bytes:
                        st.image(thumb_bytes, use_container_width=True)
                    else:
                        st.markdown("🖼️") # Fallback icon
                else:
                    st.markdown("🖼️") # Fallback icon

                st.text_input("Title", value=suggestion.vlm_title, key=f"title_{s_id}", disabled=True)

                # Use the standardized metadata formatting
                metadata = format_suggestion_metadata(suggestion, include_counts=True)
                st.caption(f"ID: {s_id} | {metadata}")

                if is_enriching:
                    st.info("AI is analyzing...", icon="⏳")
                elif suggestion.status == 'pending_enrichment':
                    action_col1, action_col2 = UILayoutManager.action_buttons(2)
                    is_checked = s_id in ui_state.suggestions_to_enrich
                    action_col1.checkbox("Select", value=is_checked, key=f"cb_{s_id}", on_change=lambda sid=s_id: toggle_enrich_selection(sid))
                    if action_col2.button("View", key=f"view_{s_id}", use_container_width=True):
                        switch_to_album_view(s_id)
                else: # 'pending' or 'enrichment_failed'
                    if st.button("✅ Review & Approve", key=f"review_{s_id}", use_container_width=True, type="primary"):
                        switch_to_album_view(s_id)

def toggle_enrich_selection(suggestion_id):
    """Callback to add/remove a suggestion from the bulk enrichment set."""
    if suggestion_id in ui_state.suggestions_to_enrich:
        ui_state.suggestions_to_enrich.remove(suggestion_id)
    else:
        ui_state.suggestions_to_enrich.add(suggestion_id)


def render_album_view(suggestion: SuggestionAlbum):
    """Renders the main detailed view for a single album suggestion."""
    # --- Editable Title ---
    current_title = suggestion.vlm_title or ''
    new_title = st.text_input("Album Title", value=current_title, key="album_title_edit")
    
    # Update title in database if changed
    if new_title != current_title and new_title.strip():
        try:
            db_service.update_suggestion_title(suggestion.id, new_title.strip())
            st.toast("Title updated!", icon="✅")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to update title: {e}")
    
    # --- Metadata Display ---
    # Use the standardized metadata formatting
    metadata = format_suggestion_metadata(suggestion, include_counts=True)
    st.caption(metadata)
    
    # Keep track of weak assets for later use
    if suggestion.status == 'from_immich':
        weak_ids = []  # No weak assets for existing albums
    else:
        weak_ids = suggestion.weak_asset_ids
    st.divider()
    
    # --- Cover Selection Mode ---
    cover_col, action_spacer = st.columns([2, 1])
    with cover_col:
        if ui_state.cover_selection_mode:
            st.warning("🖼️ **Cover Selection Mode Active** - Click on any photo below to set it as the album cover")
            if st.button("❌ Cancel Cover Selection", use_container_width=True):
                ui_state.disable_cover_selection_mode()
                st.rerun()
        else:
            if st.button("🖼️ Select Cover Picture", use_container_width=True):
                ui_state.enable_cover_selection_mode()
                st.rerun()
    
    # --- Action Buttons ---
    render_album_actions(suggestion)
    st.divider()

    # --- Photo Galleries ---
    strong_ids = suggestion.strong_asset_ids
    if suggestion.status == 'from_immich':
        # For existing Immich albums, show existing photos and potential additions
        st.subheader(f"Current Album Photos ({len(strong_ids)})")
        render_photo_grid(strong_ids, suggestion.cover_asset_id)
        
        # Show potential additions if any
        additional_assets = suggestion.additional_asset_ids
        if additional_assets:
            st.divider()
            st.subheader(f"Potential Additions ({len(additional_assets)})")
            st.info("These photos were found nearby in time and location and could be added to this existing album.")
            render_photo_grid(additional_assets, None)
    else:
        # Regular workflow for new suggestions
        st.subheader("Core Photos")
        render_photo_grid(strong_ids, suggestion.cover_asset_id)
        
        if weak_ids:
            st.divider()
            render_weak_asset_selector(weak_ids)


def render_album_actions(suggestion: SuggestionAlbum):
    """Renders the main action buttons for an album (Approve, Reject, etc.)."""
    s_id = suggestion.id
    is_enriching = process_service.is_running(f"enrich_{s_id}") or suggestion.status == 'enriching'

    if is_enriching:
        st.info("This album is currently being analyzed by the AI. Please wait.", icon="⏳")
        return

    # Layout for action buttons
    if suggestion.status == 'from_immich':
        # Special handling for existing Immich albums
        cols = st.columns(3)
        
        # Add Photos Button - for existing albums with potential additions
        additional_assets = suggestion.additional_asset_ids
        has_additions = len(additional_assets) > 0
        
        add_button_text = f"➕ Add {len(additional_assets)} Photos" if has_additions else "➕ No New Photos"
        if cols[0].button(add_button_text, type="primary" if has_additions else "secondary", 
                         use_container_width=True, disabled=not has_additions):
            handle_add_photos_action(suggestion)
        
        # Hide Album Button (equivalent to reject for existing albums)
        if cols[1].button("👁️‍🗨️ Hide Album", use_container_width=True):
            handle_reject_action(s_id)
        
        # Back to List Button
        if cols[2].button("⬅️ Back to List", use_container_width=True):
            ui_state.selected_suggestion_id = None
            st.rerun()
    else:
        # Regular workflow for new suggestions
        cols = UILayoutManager.photo_grid_layout(4)
        
        # Approve Button - enable for both pending_enrichment and pending statuses
        can_create_album = suggestion.status in ['pending', 'pending_enrichment']
        if cols[0].button("✅ Create Album in Immich", type="primary", use_container_width=True, disabled=not can_create_album):
            handle_approve_action(suggestion)

        # Reject Button
        if cols[1].button("❌ Reject Suggestion", use_container_width=True):
            handle_reject_action(s_id)

        # Enrich/Re-enrich Button
        enrich_text = "✨ Re-run AI Analysis" if suggestion.status == 'pending' else "✨ Run AI Analysis"
        if cols[2].button(enrich_text, use_container_width=True):
            process_service.start_enrichment(s_id)
            st.toast("Enrichment process started!", icon="✨")
            st.rerun()

        # Back to List Button
        if cols[3].button("⬅️ Back to List", use_container_width=True):
            ui_state.selected_suggestion_id = None
            st.rerun()


def handle_approve_action(suggestion: SuggestionAlbum):
    """Logic for when a user approves a suggestion."""
    with st.spinner("Creating album in Immich... This may take a moment."):
        try:
            strong_assets = suggestion.strong_asset_ids
            final_asset_ids = strong_assets + list(ui_state.included_weak_assets)
            
            success = immich_service.create_album(
                title=suggestion.vlm_title,
                asset_ids=final_asset_ids,
                cover_asset_id=suggestion.cover_asset_id,
                highlight_ids=[] # Highlight logic can be added later
            )
            
            if success:
                db_service.update_suggestion_status(suggestion.id, 'approved')
                st.success(f"Album '{suggestion.vlm_title}' created successfully in Immich!")
                ui_state.selected_suggestion_id = None
                time.sleep(2) # Give user time to read the success message
                st.rerun()
            else:
                st.error("Album creation failed in Immich. Check the application logs for details.")
        except AppServiceError as e:
            logger.error(f"Service error during album creation: {e}", exc_info=True)
            st.error(f"An error occurred: {e}")


def handle_reject_action(suggestion_id: int):
    """Logic for when a user rejects a suggestion."""
    try:
        db_service.update_suggestion_status(suggestion_id, 'rejected')
        st.warning("Suggestion has been rejected and will be hidden.")
        ui_state.selected_suggestion_id = None
        time.sleep(2)
        st.rerun()
    except AppServiceError as e:
        logger.error(f"Service error during suggestion rejection: {e}", exc_info=True)
        st.error(f"An error occurred while rejecting: {e}")


def handle_add_photos_action(suggestion: SuggestionAlbum):
    """Logic for adding photos to an existing Immich album."""
    try:
        album_id = suggestion.immich_album_id
        additional_assets = suggestion.additional_asset_ids
        album_title = suggestion.vlm_title or 'Unknown Album'
        
        if not album_id or not additional_assets:
            st.error("No photos to add or album information missing.")
            return
        
        with st.spinner(f"Adding {len(additional_assets)} photos to album '{album_title}'..."):
            # Use the existing Immich API to add assets to the album
            from app.immich_api import add_assets_to_album
            
            success = add_assets_to_album(album_id, additional_assets)
            
            if success:
                db_service.update_suggestion_status(suggestion.id, 'approved')
                st.success(f"Successfully added {len(additional_assets)} photos to album '{album_title}'!")
                ui_state.selected_suggestion_id = None
                time.sleep(2)
                st.rerun()
            else:
                st.error("Failed to add photos to the album. Please check the logs.")
                
    except Exception as e:
        logger.error(f"Service error during photo addition: {e}", exc_info=True)
        st.error(f"An error occurred while adding photos: {e}")


def handle_merge_suggestions(suggestion_ids: list[int]):
    """Logic for merging multiple suggestions into one."""
    logger.info(f"handle_merge_suggestions called with {suggestion_ids}")
    
    if len(suggestion_ids) < 2:
        st.error("Please select at least 2 suggestions to merge.")
        return
    
    try:
        logger.info(f"Getting suggestion details for {suggestion_ids}")
        # Get suggestion details for display
        suggestions = []
        for s_id in suggestion_ids:
            suggestion = db_service.get_suggestion_details(s_id)
            if suggestion:
                suggestions.append(suggestion)
        
        logger.info(f"Found {len(suggestions)} valid suggestions out of {len(suggestion_ids)} requested")
        
        if len(suggestions) != len(suggestion_ids):
            logger.error(f"Missing suggestions: requested {suggestion_ids}, found {[s['id'] for s in suggestions]}")
            st.error("Some selected suggestions could not be found.")
            return
        
        # Create a unique merge session key
        merge_key = f"merge_{'-'.join(map(str, sorted(suggestion_ids)))}"
        st.session_state.setdefault(f"{merge_key}_confirmed", False)
        
        logger.info(f"Merge key: {merge_key}, confirmed status: {st.session_state.get(f'{merge_key}_confirmed', False)}")
        
        if not st.session_state.get(f"{merge_key}_confirmed", False):
            logger.info("Showing confirmation dialog")
            # Calculate merged info for preview
            total_photos = 0
            titles = []
            for suggestion in suggestions:
                strong_ids = suggestion.strong_asset_ids
                weak_ids = suggestion.weak_asset_ids
                total_photos += len(strong_ids) + len(weak_ids)
                if suggestion.vlm_title:
                    titles.append(suggestion.vlm_title)
            
            # Show confirmation dialog at the top of the page
            st.error("⚠️ **MERGE CONFIRMATION REQUIRED**")
            
            with st.container():
                st.write(f"**Merging {len(suggestions)} suggestions into 1 album:**")
                
                # Show titles in a more compact format
                title_list = []
                for suggestion in suggestions:
                    title = suggestion.vlm_title or 'Untitled'
                    if len(title) > 30:
                        title = title[:27] + "..."
                    title_list.append(title)
                
                st.write("• " + " • ".join(title_list))
                st.write(f"**Total photos:** {total_photos}")
                
                col1, col2, _ = st.columns([1, 1, 2])
                
                if col1.button("✅ Confirm", type="primary", key=f"{merge_key}_confirm", use_container_width=True):
                    logger.info(f"Merge confirmation button clicked for {suggestion_ids}")
                    st.session_state[f"{merge_key}_confirmed"] = True
                    # Don't rerun here, let it continue to the merge logic
                    
                if col2.button("❌ Cancel", key=f"{merge_key}_cancel", use_container_width=True):
                    ui_state.suggestions_to_enrich.clear()
                    # Clean up confirmation state
                    if f"{merge_key}_confirmed" in st.session_state:
                        del st.session_state[f"{merge_key}_confirmed"]
                    st.rerun()
                    
                # If not confirmed yet, stop here
                if not st.session_state.get(f"{merge_key}_confirmed", False):
                    st.stop()
        
        # If we get here, merge was confirmed - proceed with merge
        logger.info("Merge confirmed, proceeding with merge operation")
        
        # Perform the merge with detailed logging
        try:
            st.info("🔄 Processing merge...")
            logger.info(f"Starting merge of suggestions: {suggestion_ids}")
            
            merged_id = db_service.merge_suggestions(suggestion_ids)
            
            logger.info(f"Merge completed successfully. New ID: {merged_id}")
            
            # Clean up confirmation state
            if f"{merge_key}_confirmed" in st.session_state:
                del st.session_state[f"{merge_key}_confirmed"]
            
            # Clear the selection since merge is complete
            ui_state.suggestions_to_enrich.clear()
            
            # Switch to viewing the merged suggestion
            ui_state.selected_suggestion_id = merged_id
            ui_state.view_mode = 'album'
            
            st.success(f"✅ Successfully merged {len(suggestion_ids)} suggestions into one album!")
            st.toast(f"Successfully merged {len(suggestion_ids)} suggestions!", icon="🔗")
            
            # Force a rerun to update the UI
            time.sleep(1)  # Brief pause to show success message
            st.rerun()
            
        except Exception as merge_error:
            logger.error(f"Merge failed for suggestions {suggestion_ids}: {merge_error}", exc_info=True)
            st.error(f"❌ Merge failed: {str(merge_error)}")
            
            # Clean up confirmation state on error
            if f"{merge_key}_confirmed" in st.session_state:
                del st.session_state[f"{merge_key}_confirmed"]
        
    except Exception as e:
        logger.error(f"Error merging suggestions {suggestion_ids}: {e}", exc_info=True)
        st.error(f"Failed to merge suggestions: {e}")
        # Clean up any confirmation state on error
        merge_key = f"merge_{'-'.join(map(str, sorted(suggestion_ids)))}"
        if f"{merge_key}_confirmed" in st.session_state:
            del st.session_state[f"{merge_key}_confirmed"]


def render_photo_grid(asset_ids: list[str], cover_id: str | None):
    """Renders a responsive grid of photo thumbnails with pagination."""
    if not asset_ids:
        st.info("No photos to display in this section.")
        return

    # Get configurable pagination settings
    items_per_page = config.get('ui.thumbnails_per_page', 50)
    num_columns = config.get('ui.gallery_columns', 6)
    
    # Configure jump button for cover photo
    jump_config = None
    if cover_id and cover_id in asset_ids:
        cover_index = asset_ids.index(cover_id)
        cover_page = cover_index // items_per_page
        current_page = getattr(ui_state, 'core_photos_page', 0)
        jump_config = {
            'text': '📷 Cover',
            'target_page': cover_page,
            'condition': cover_page != current_page,
            'help': 'Go to cover photo'
        }
    
    # Use the generalized pagination function
    page_asset_ids, has_pagination = render_pagination_controls(
        asset_ids, 
        items_per_page, 
        'core_photos_page', 
        ui_state,
        show_jump_button=True,
        jump_button_config=jump_config
    )
    
    if not has_pagination:
        st.caption(f"All {len(asset_ids)} photos")
    
    # Render grid of photos for current page
    for i in range(0, len(page_asset_ids), num_columns):
        cols = st.columns(num_columns)
        for j, asset_id in enumerate(page_asset_ids[i : i + num_columns]):
            with cols[j]:
                # Use the unified thumbnail rendering function
                caption = "Cover" if asset_id == cover_id else ""
                action_taken, action_type = render_thumbnail_card(
                    asset_id=asset_id,
                    get_cached_thumbnail_func=get_cached_thumbnail,
                    get_photo_metadata_func=get_photo_metadata,
                    show_metadata=True,
                    cover_selection_mode=ui_state.cover_selection_mode,
                    current_cover_id=cover_id,
                    container=cols[j],
                    button_prefix="grid_",
                    thumbnail_caption=caption
                )
                
                # Handle the actions returned by the thumbnail card
                if action_taken:
                    if action_type == 'cover':
                        # Update cover in database
                        db_service.update_suggestion_cover(ui_state.selected_suggestion_id, asset_id)
                        ui_state.disable_cover_selection_mode()
                        st.success(f"✅ Cover updated successfully!")
                        st.rerun()
                    elif action_type == 'view':
                        # Switch to photo view
                        st.session_state.selected_asset_id = asset_id
                        ui_state.view_mode = 'photo'
                        st.rerun()


def render_weak_asset_selector(weak_asset_ids: list[str]):
    """Renders the UI for selecting which 'additional' photos to include."""
    st.subheader(f"Review Additional Photos ({len(weak_asset_ids)})")
    st.info("These photos are related, but further in time or location. Select any you wish to include in the final album.")
    
    # Toggle all checkbox with optimized callback
    def toggle_all_weak_assets():
        if st.session_state.get('select_all_weak', False):
            # Bulk update without triggering individual widget updates
            ui_state.included_weak_assets.update(weak_asset_ids)
            # Update all individual checkbox states efficiently
            for asset_id in weak_asset_ids:
                st.session_state[f"cb_weak_{asset_id}"] = True
        else:
            ui_state.included_weak_assets.clear()
            # Clear all individual checkbox states efficiently  
            for asset_id in weak_asset_ids:
                st.session_state[f"cb_weak_{asset_id}"] = False
    
    # Show current selection summary
    total_selected = len(ui_state.included_weak_assets.intersection(set(weak_asset_ids)))
    
    col1, col2 = st.columns([2, 1])
    with col1:
        st.checkbox("Include all additional photos", key="select_all_weak", on_change=toggle_all_weak_assets)
    with col2:
        st.caption(f"Selected: {total_selected}/{len(weak_asset_ids)}")
    
    # Use the generalized pagination function
    items_per_page = config.get('ui.thumbnails_per_page', 50)
    page_asset_ids, _ = render_pagination_controls(
        weak_asset_ids, 
        items_per_page, 
        'weak_assets_page', 
        ui_state
    )
    
    # Render grid of checkboxes for individual selection
    num_columns = config.get('ui.gallery_columns', 6)
    for i in range(0, len(page_asset_ids), num_columns):
        cols = st.columns(num_columns)
        for j, asset_id in enumerate(page_asset_ids[i : i + num_columns]):
            with cols[j]:
                # Use the unified thumbnail rendering function for display
                action_taken, action_type = render_thumbnail_card(
                    asset_id=asset_id,
                    get_cached_thumbnail_func=get_cached_thumbnail,
                    get_photo_metadata_func=get_photo_metadata,
                    show_metadata=True,
                    cover_selection_mode=False,
                    container=cols[j],
                    button_prefix="weak_"
                )
                
                # Handle view action from thumbnail card
                if action_taken and action_type == 'view':
                    st.session_state.selected_asset_id = asset_id
                    ui_state.view_mode = 'photo'
                    st.rerun()
                
                # Add the include checkbox below the thumbnail
                checkbox_key = f"cb_weak_{asset_id}"
                if checkbox_key not in st.session_state:
                    st.session_state[checkbox_key] = asset_id in ui_state.included_weak_assets
                
                if st.checkbox("Include", key=checkbox_key, label_visibility="collapsed"):
                    ui_state.included_weak_assets.add(asset_id)
                else:
                    ui_state.included_weak_assets.discard(asset_id)

# Removed toggle_weak_asset function - now using inline checkbox handling for better performance


@st.cache_data(show_spinner=False)
def get_cached_full_image(asset_id: str) -> bytes | None:
    """Cached function to fetch full-size images."""
    if not asset_id:
        return None
    try:
        return immich_service.get_full_image_bytes(asset_id)
    except Exception as e:
        logger.warning(f"Failed to fetch full image for asset {asset_id}: {e}")
        return None


def render_photo_view(suggestion: SuggestionAlbum):
    """Renders the single photo view for a selected asset."""
    asset_id = st.session_state.selected_asset_id
    
    # Back to album button
    col1, col2, _ = st.columns([1, 2, 1])
    with col1:
        if st.button("⬅️ Back to Album", use_container_width=True):
            ui_state.view_mode = 'album'
            st.session_state.selected_asset_id = None
            st.rerun()
    
    with col2:
        st.subheader(f"Photo View - {suggestion.vlm_title or 'Album'}")
    
    # Create two columns: image on left, EXIF data on right
    img_col, exif_col = st.columns([2, 1])
    
    with img_col:
        # Get full-size image with better error handling
        try:
            with st.spinner("Loading full-size image..."):
                full_image_bytes = get_cached_full_image(asset_id)
                image_loaded = False
                
                if full_image_bytes:
                    try:
                        # Test if the image bytes are valid before displaying
                        from io import BytesIO
                        test_img = Image.open(BytesIO(full_image_bytes))
                        test_img.verify()  # This will raise an exception if image is corrupted
                        
                        # If we get here, image is valid
                        st.image(full_image_bytes, use_container_width=True)
                        image_loaded = True
                    except Exception as img_error:
                        logger.warning(f"Full image corrupted for asset {asset_id}: {img_error}")
                        # Fall through to thumbnail fallback
                
                if not image_loaded:
                    # Fallback to thumbnail if full image fails or is corrupted
                    thumb_bytes = get_cached_thumbnail(asset_id)
                    if thumb_bytes:
                        try:
                            # Also verify thumbnail
                            test_thumb = Image.open(BytesIO(thumb_bytes))
                            test_thumb.verify()
                            
                            st.image(thumb_bytes, use_container_width=True)
                            st.warning("Showing thumbnail (full image unavailable or corrupted)")
                            image_loaded = True
                        except Exception as thumb_error:
                            logger.warning(f"Thumbnail also corrupted for asset {asset_id}: {thumb_error}")
                    
                    if not image_loaded:
                        st.error(f"Unable to load image for asset {asset_id}")
                        st.info("This image file may be corrupted or in an unsupported format")
                        
        except Exception as e:
            logger.error(f"Unexpected error loading photo {asset_id}: {e}")
            st.error("An unexpected error occurred while loading the photo")
            if st.button("Back to Album"):
                ui_state.view_mode = 'album'
                st.session_state.selected_asset_id = None
                st.rerun()
            return
    
    with exif_col:
        # Display EXIF data table
        st.subheader("Photo Details")
        
        try:
            exif_data = immich_service.get_exif_data(asset_id)
            if exif_data:
                # Create a clean table of important EXIF data
                display_data = {}
                
                # Camera information
                if exif_data.get('make'):
                    display_data['Camera Make'] = exif_data['make']
                if exif_data.get('model'):
                    display_data['Camera Model'] = exif_data['model']
                if exif_data.get('lens_model'):
                    display_data['Lens'] = exif_data['lens_model']
                
                # Shooting information
                if exif_data.get('f_number'):
                    display_data['Aperture'] = f"f/{exif_data['f_number']}"
                if exif_data.get('exposure_time'):
                    display_data['Shutter Speed'] = f"1/{int(1/float(exif_data['exposure_time']))}"
                if exif_data.get('iso'):
                    display_data['ISO'] = str(exif_data['iso'])
                if exif_data.get('focal_length'):
                    display_data['Focal Length'] = f"{exif_data['focal_length']}mm"
                
                # Date and time
                if exif_data.get('date_time_original'):
                    display_data['Date Taken'] = str(exif_data['date_time_original'])
                elif exif_data.get('created_at'):
                    display_data['Date Added'] = str(exif_data['created_at'])
                
                # Location information
                if exif_data.get('latitude') and exif_data.get('longitude'):
                    lat = float(exif_data['latitude'])
                    lon = float(exif_data['longitude'])
                    
                    # Try to get city and country from coordinates
                    try:
                        from app import geocoding
                        location_name = geocoding.get_location_from_coordinates(lat, lon)
                        if location_name:
                            display_data['Location'] = location_name
                        else:
                            # Fallback to GPS coordinates if geocoding fails
                            display_data['GPS'] = f"{lat:.6f}, {lon:.6f}"
                    except Exception:
                        # If geocoding fails, show GPS coordinates
                        display_data['GPS'] = f"{lat:.6f}, {lon:.6f}"
                
                # File information
                if exif_data.get('file_size_bytes'):
                    size_mb = int(exif_data['file_size_bytes']) / (1024 * 1024)
                    display_data['File Size'] = f"{size_mb:.1f} MB"
                
                # Display as a clean table
                for key, value in display_data.items():
                    st.text(f"{key}: {value}")
                
                st.caption(f"Asset ID: {asset_id}")
            else:
                st.info("No EXIF data available")
                st.caption(f"Asset ID: {asset_id}")
                
        except Exception as e:
            st.error(f"Failed to load EXIF data: {e}")
            st.caption(f"Asset ID: {asset_id}")
    
    # Navigation controls at the bottom
    st.divider()
    
    # Navigation within album
    strong_ids = suggestion.strong_asset_ids
    weak_ids = suggestion.weak_asset_ids
    all_ids = strong_ids + weak_ids
    
    if asset_id in all_ids:
        current_index = all_ids.index(asset_id)
        
        nav_col1, nav_col2, nav_col3 = st.columns(3)
        
        with nav_col1:
            if current_index > 0 and st.button("⬅️ Previous", use_container_width=True):
                st.session_state.selected_asset_id = all_ids[current_index - 1]
                st.rerun()
                
        with nav_col2:
            st.write(f"Photo {current_index + 1} of {len(all_ids)}")
            
        with nav_col3:
            if current_index < len(all_ids) - 1 and st.button("Next ➡️", use_container_width=True):
                st.session_state.selected_asset_id = all_ids[current_index + 1]
                st.rerun()

# --- Section 3: Main Application Logic ---

def render_suggestions_table_view():
    """Renders a table view of all pending suggestions when no album is selected."""
    
    # Check for merge intent first
    if ui_state.has_merge_intent():
        merge_intent = ui_state.get_merge_intent()
        logger.info(f"Processing merge intent for {merge_intent}")
        handle_merge_suggestions(merge_intent)
        # Clear the intent after processing
        ui_state.clear_merge_intent()
        return
    
    # Header with title and stats
    suggestions = db_service.get_pending_suggestions(
        sort_by=ui_state.sort_by, 
        sort_order=ui_state.sort_order
    )
    
    col1, col2 = st.columns([3, 1])
    with col1:
        st.header("📋 Pending Album Suggestions")
    with col2:
        st.metric("Total Suggestions", len(suggestions))
    
    # --- Top Controls Row ---
    st.markdown("---")
    
    # Bulk action buttons row
    col1, col2, col3, col4, col5, col6 = st.columns([1, 1, 1, 1, 1, 2])
    
    # Bulk actions
    with col1:
        if st.button("✨ Enrich Selected", disabled=not ui_state.suggestions_to_enrich, use_container_width=True):
            for s_id in list(ui_state.suggestions_to_enrich):
                process_service.start_enrichment(s_id)
            ui_state.suggestions_to_enrich.clear()
            st.toast("Enrichment process(es) started!", icon="✨")
            st.rerun()
    
    with col2:
        if st.button("🔗 Merge Selected", disabled=len(ui_state.suggestions_to_enrich) < 2, use_container_width=True):
            # Set merge intent instead of calling handler directly
            ui_state.set_merge_intent(list(ui_state.suggestions_to_enrich))
            st.rerun()
    
    with col3:
        if st.button("Clear Selection", use_container_width=True):
            ui_state.suggestions_to_enrich.clear()
            st.rerun()
    
    # Delete all button with confirmation
    with col4:
        if 'confirm_delete_all_table' not in st.session_state:
            st.session_state.confirm_delete_all_table = False
        
        if not st.session_state.confirm_delete_all_table:
            if st.button("🗑️ Delete All", type="secondary", use_container_width=True):
                st.session_state.confirm_delete_all_table = True
                st.rerun()
        else:
            if st.button("✅ Confirm Delete", type="primary", use_container_width=True):
                try:
                    deleted_count = db_service.delete_all_pending_suggestions()
                    if deleted_count > 0:
                        st.toast(f"Deleted {deleted_count} pending suggestions!", icon="🗑️")
                        ui_state.selected_suggestion_id = None
                        ui_state.suggestions_to_enrich.clear()
                    else:
                        st.toast("No pending suggestions to delete", icon="ℹ️")
                    st.session_state.confirm_delete_all_table = False
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to delete suggestions: {e}")
                    st.session_state.confirm_delete_all_table = False
    
    # Scan buttons
    with col5:
        if st.button("🔄 Incremental Scan", use_container_width=True):
            process_service.start_clustering_scan('incremental')
            st.toast("Incremental scan started!", icon="🔄")
    
    with col6:
        scan_col1, scan_col2, cancel_col = st.columns([1, 1, 1])
        with scan_col1:
            if st.button("🔄 Full Scan", use_container_width=True):
                process_service.start_clustering_scan('full')
                st.toast("Full scan started!", icon="🔄")
        with scan_col2:
            pass  # Empty for spacing
        with cancel_col:
            if st.button("❌ Cancel Delete", use_container_width=True) and st.session_state.confirm_delete_all_table:
                st.session_state.confirm_delete_all_table = False
                st.rerun()
    
    if not suggestions:
        st.info("No pending suggestions. Run a scan to find new album candidates!")
        return
    
    # --- Table Header with Sorting ---
    st.markdown("---")
    
    # Create sortable column headers
    header_cols = st.columns([0.5, 1, 2, 2, 1.5, 1.5, 1, 1])
    
    with header_cols[0]:
        st.markdown("**☑️**")  # Checkbox column
    
    with header_cols[1]:
        st.markdown("**📷**")  # Thumbnail column
    
    with header_cols[2]:
        if st.button("**📝 Title**", key="sort_title", use_container_width=True):
            # Title sorting not implemented in DB yet, but we can add it
            st.toast("Title sorting not yet implemented", icon="ℹ️")
    
    with header_cols[3]:
        if st.button("**📍 Location**", key="sort_location", use_container_width=True):
            # Location sorting not implemented in DB yet
            st.toast("Location sorting not yet implemented", icon="ℹ️")
    
    with header_cols[4]:
        sort_icon = "🔽" if ui_state.sort_by == "event_start_date" and ui_state.sort_order == "desc" else "🔼" if ui_state.sort_by == "event_start_date" else ""
        if st.button(f"**📅 Date** {sort_icon}", key="sort_date", use_container_width=True):
            ui_state.toggle_sort("event_start_date")
            st.rerun()
    
    with header_cols[5]:
        sort_icon = "🔽" if ui_state.sort_by == "image_count" and ui_state.sort_order == "desc" else "🔼" if ui_state.sort_by == "image_count" else ""
        if st.button(f"**📊 Photos** {sort_icon}", key="sort_photos", use_container_width=True):
            ui_state.toggle_sort("image_count")
            st.rerun()
    
    with header_cols[6]:
        st.markdown("**📊 Status**")
    
    with header_cols[7]:
        st.markdown("**⚡ Actions**")
    
    st.markdown("---")
    
    # --- Table Rows ---
    for suggestion in suggestions:
        s_id = suggestion.id
        is_enriching = process_service.is_running(f"enrich_{s_id}") or suggestion.status == 'enriching'
        
        cols = st.columns([0.5, 1, 2, 2, 1.5, 1.5, 1, 1])
        
        # Checkbox
        with cols[0]:
            is_selected = s_id in ui_state.suggestions_to_enrich
            if st.checkbox("Select", value=is_selected, key=f"table_select_{s_id}", label_visibility="collapsed"):
                if s_id not in ui_state.suggestions_to_enrich:
                    ui_state.suggestions_to_enrich.add(s_id)
            else:
                ui_state.suggestions_to_enrich.discard(s_id)
        
        # Thumbnail
        with cols[1]:
            cover_id = suggestion.cover_asset_id
            if not cover_id:
                strong_ids = suggestion.strong_asset_ids
                cover_id = strong_ids[0] if strong_ids else None
            
            thumb_bytes = get_cached_thumbnail(cover_id)
            if thumb_bytes:
                st.image(thumb_bytes, width=80)
            else:
                st.markdown("🖼️")
        
        # Title
        with cols[2]:
            title = suggestion.vlm_title or 'Untitled'
            st.markdown(f"**{title}**")
        
        # Location
        with cols[3]:
            location = suggestion.location or 'Unknown location'
            st.text(location)
        
        # Date
        with cols[4]:
            date_text = format_date_range(suggestion.event_start_date, suggestion.event_end_date)
            if date_text == "Unknown date":
                date_text = "Unknown"
            st.text(date_text)
        
        # Photo count - use standardized formatting but extract just the count part
        with cols[5]:
            metadata = format_suggestion_metadata(suggestion, include_counts=True)
            # Extract just the photo count part (first part before first |)
            photo_part = metadata.split(" | ")[0] if " | " in metadata else metadata
            # Remove the word "photos" to make it more compact for table
            photo_text = photo_part.replace(" photos", "")
            st.text(photo_text)
        
        # Status
        with cols[6]:
            status = suggestion.status
            status_emoji = {
                'pending_enrichment': '⏳',
                'enriching': '🔄',
                'pending': '✅',
                'enrichment_failed': '❌',
                'from_immich': '📱'
            }.get(status, '❓')
            
            if is_enriching:
                st.markdown(f"{status_emoji} Enriching...")
            else:
                st.markdown(f"{status_emoji} {status.replace('_', ' ').title()}")
        
        # Actions
        with cols[7]:
            if is_enriching:
                st.text("Processing...")
            else:
                if st.button("👁️ View", key=f"table_view_{s_id}", use_container_width=True):
                    switch_to_album_view(s_id)
                    st.rerun()
        
        st.markdown("---")


def main():
    """The main function that orchestrates the rendering of the UI."""
    st.set_page_config(layout="wide", page_title=config.get('ui.page_title', "Album Suggester"))

    # Initialize session state if it's the first run.
    init_session_state()
    
    # Check if scan is running and auto-refresh
    is_scanning = process_service.is_running('scan')
    if is_scanning:
        st.info("🚀 Scan in progress... (auto-refreshing)")
        time.sleep(2)  # Auto-refresh every 2 seconds while scanning
        st.rerun()
    
    # --- Sidebar ---
    with st.sidebar:
        render_suggestion_list()
        st.divider()
        render_scan_controls()

    # --- Main Content Area ---
    selected_id = ui_state.selected_suggestion_id
    if selected_id is None:
        # If no album is selected, show the pending suggestions table view.
        render_suggestions_table_view()
    else:
        # If an album is selected, fetch its details and render the main view.
        suggestion = db_service.get_suggestion_details(selected_id)
        if suggestion:
            # Check if enrichment process is running and add periodic refresh
            is_enriching = process_service.is_running(f"enrich_{selected_id}") or suggestion.status == 'enriching'
            if is_enriching:
                # Auto-refresh every 3 seconds while enrichment is running
                time.sleep(3)
                st.rerun()
            if ui_state.view_mode == 'photo' and st.session_state.selected_asset_id:
                render_photo_view(suggestion)
            else:
                render_album_view(suggestion)
        else:
            # This can happen if the suggestion was deleted in another session.
            st.error(f"Suggestion with ID {selected_id} not found. It may have been deleted.")
            ui_state.selected_suggestion_id = None
            time.sleep(2)
            st.rerun()

if __name__ == "__main__":
    try:
        main()
    except AppServiceError as e:
        # A catch-all for our custom service errors to show a friendly message.
        logger.critical(f"A critical service error was not handled gracefully: {e}", exc_info=True)
        st.error(f"A critical application error occurred: {e}. Please check the logs and restart the application.")
    except Exception as e:
        # Catch any other unexpected errors.
        logger.critical(f"An unexpected error occurred in the UI: {e}", exc_info=True)
        st.error("An unexpected error occurred. Please check the logs.")