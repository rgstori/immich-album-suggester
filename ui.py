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
import json
import logging
import math
import time
from PIL import Image, ImageOps
from io import BytesIO

# Import the services that will handle all the heavy lifting.
from app.services import db_service, immich_service, process_service
# Using an alias for our exception base class for cleaner code.
from app.exceptions import AppServiceError

# Initialize the logger for this UI module.
logger = logging.getLogger(__name__)


# --- Section 1: UI State and Cache Management ---

def init_session_state():
    """
    Initializes all necessary keys in Streamlit's session state.
    This function is the single source of truth for the UI's state variables,
    preventing `AttributeError` exceptions and ensuring a clean start.
    """
    # Tracks the currently selected suggestion for the main view.
    st.session_state.setdefault("selected_suggestion_id", None)
    
    # Manages the detailed view for a single asset.
    st.session_state.setdefault("selected_asset_id", None)
    st.session_state.setdefault("view_mode", "album")  # Can be 'album' or 'photo'
    
    # State for the gallery pagination.
    st.session_state.setdefault("gallery_page", 0)
    
    # Tracks which "additional" photos are selected for inclusion.
    st.session_state.setdefault("included_weak_assets", set())
    
    # Manages the checkbox state for bulk enrichment.
    st.session_state.setdefault("suggestions_to_enrich", set())
    
    # Sorting options for pending suggestions
    st.session_state.setdefault("sort_by", "image_count")
    st.session_state.setdefault("sort_order", "desc")

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

def switch_to_album_view(suggestion_id: int):
    """
    Callback to cleanly switch the main view to a specific album.
    Resets all relevant session state to ensure the new album displays correctly.
    """
    # Clear state related to the previously viewed album.
    if st.session_state.selected_suggestion_id != suggestion_id:
        st.session_state.gallery_page = 0
        st.session_state.included_weak_assets = set()
        # Reset pagination for both core photos and weak assets
        st.session_state.core_photos_page = 0
        st.session_state.weak_assets_page = 0

    st.session_state.selected_suggestion_id = suggestion_id
    st.session_state.view_mode = 'album'
    
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
                    st.session_state.selected_suggestion_id = None
                    st.session_state.suggestions_to_enrich.clear()
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
            index=list(sort_options.keys()).index(st.session_state.sort_by),
            key="sort_by_select",
            label_visibility="collapsed"
        )
    
    with sort_col2:
        sort_order = st.selectbox(
            "Order", 
            options=["desc", "asc"],
            format_func=lambda x: "High→Low" if x == "desc" else "Low→High",
            index=0 if st.session_state.sort_order == "desc" else 1,
            key="sort_order_select",
            label_visibility="collapsed"
        )
    
    # Update session state if changed
    if sort_by != st.session_state.sort_by or sort_order != st.session_state.sort_order:
        st.session_state.sort_by = sort_by
        st.session_state.sort_order = sort_order
        st.rerun()
    
    # Fetch suggestions with sorting
    suggestions = db_service.get_pending_suggestions(sort_by=st.session_state.sort_by, sort_order=st.session_state.sort_order)

    if not suggestions:
        st.sidebar.info("No pending suggestions. Run a scan!")
        return

    # --- Bulk Action Controls ---
    st.sidebar.markdown("---")
    st.sidebar.write("**Bulk Actions**")
    
    # First row: Enrich and Clear
    col1, col2 = st.sidebar.columns(2)
    if col1.button("✨ Enrich Selected", use_container_width=True, disabled=not st.session_state.suggestions_to_enrich):
        for s_id in list(st.session_state.suggestions_to_enrich):
            process_service.start_enrichment(s_id)
        st.session_state.suggestions_to_enrich.clear()
        st.toast("Enrichment process(es) started!", icon="✨")
        st.rerun()

    if col2.button("Clear Selection", use_container_width=True):
        st.session_state.suggestions_to_enrich.clear()
        st.rerun()
    
    # Second row: Merge button
    if st.sidebar.button("🔗 Merge Selected", use_container_width=True, disabled=len(st.session_state.suggestions_to_enrich) < 2):
        # Set merge intent instead of calling handler directly
        st.session_state.merge_intent = list(st.session_state.suggestions_to_enrich)
        st.rerun()

    st.sidebar.markdown("---")

    # --- Scrollable Suggestions Container ---
    with st.sidebar.container(height=600, border=False):
        # --- Render Individual Suggestion Cards ---
        for suggestion in suggestions:
            s_id = suggestion['id']
            is_enriching = process_service.is_running(f"enrich_{s_id}") or suggestion['status'] == 'enriching'

            with st.container(border=True):
                # Use cover photo if available, otherwise first strong asset.
                cover_id = suggestion.get('cover_asset_id')
                if not cover_id:
                    strong_ids = json.loads(suggestion.get('strong_asset_ids_json', '[]'))
                    cover_id = strong_ids[0] if strong_ids else None
                
                thumb_bytes = get_cached_thumbnail(cover_id)
                if thumb_bytes:
                    st.image(thumb_bytes, use_container_width=True)
                else:
                    st.markdown("🖼️") # Fallback icon

                st.text_input("Title", value=suggestion.get('vlm_title'), key=f"title_{s_id}", disabled=True)

                # Calculate photo counts
                strong_ids = json.loads(suggestion.get('strong_asset_ids_json', '[]'))
                weak_ids = json.loads(suggestion.get('weak_asset_ids_json', '[]'))
                core_count = len(strong_ids)
                additional_count = len(weak_ids)
                
                # Display photo count with additional photos format
                if additional_count > 0:
                    photo_text = f"{core_count} (+{additional_count}) photos"
                else:
                    photo_text = f"{core_count} photos"
                
                # Format date range
                start_date = suggestion.get('event_start_date')
                end_date = suggestion.get('event_end_date')
                date_text = ""
                
                if start_date:
                    try:
                        from datetime import datetime
                        if isinstance(start_date, str):
                            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                        else:
                            start_dt = start_date
                        
                        start_formatted = start_dt.strftime('%d-%m-%y')
                        
                        if end_date:
                            if isinstance(end_date, str):
                                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                            else:
                                end_dt = end_date
                            
                            # Only show end date if it's different from start date
                            if start_dt.date() != end_dt.date():
                                end_formatted = end_dt.strftime('%d-%m-%y')
                                date_text = f"{start_formatted} - {end_formatted}"
                            else:
                                date_text = start_formatted
                        else:
                            date_text = start_formatted
                    except (ValueError, AttributeError):
                        date_text = ""
                
                # Display location
                location = suggestion.get('location') or "Unknown location"
                
                # Combine all info - ensure all parts are strings
                info_parts = [f"ID: {s_id}", photo_text]
                if date_text:
                    info_parts.append(date_text)
                if location:
                    info_parts.append(location)
                
                # Filter out any None values and ensure strings
                info_parts = [str(part) for part in info_parts if part is not None]
                
                st.caption(" | ".join(info_parts))

                if is_enriching:
                    st.info("AI is analyzing...", icon="⏳")
                elif suggestion['status'] == 'pending_enrichment':
                    action_col1, action_col2 = st.columns(2)
                    is_checked = s_id in st.session_state.suggestions_to_enrich
                    action_col1.checkbox("Select", value=is_checked, key=f"cb_{s_id}", on_change=lambda sid=s_id: toggle_enrich_selection(sid))
                    if action_col2.button("View", key=f"view_{s_id}", use_container_width=True):
                        switch_to_album_view(s_id)
                else: # 'pending' or 'enrichment_failed'
                    if st.button("✅ Review & Approve", key=f"review_{s_id}", use_container_width=True, type="primary"):
                        switch_to_album_view(s_id)

def toggle_enrich_selection(suggestion_id):
    """Callback to add/remove a suggestion from the bulk enrichment set."""
    if suggestion_id in st.session_state.suggestions_to_enrich:
        st.session_state.suggestions_to_enrich.remove(suggestion_id)
    else:
        st.session_state.suggestions_to_enrich.add(suggestion_id)


def render_album_view(suggestion: dict):
    """Renders the main detailed view for a single album suggestion."""
    # --- Editable Title ---
    current_title = suggestion.get('vlm_title', '')
    new_title = st.text_input("Album Title", value=current_title, key="album_title_edit")
    
    # Update title in database if changed
    if new_title != current_title and new_title.strip():
        try:
            db_service.update_suggestion_title(suggestion['id'], new_title.strip())
            st.toast("Title updated!", icon="✅")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to update title: {e}")
    
    # --- Metadata Display ---
    strong_ids = json.loads(suggestion.get('strong_asset_ids_json', '[]'))
    weak_ids = json.loads(suggestion.get('weak_asset_ids_json', '[]'))
    core_count = len(strong_ids)
    additional_count = len(weak_ids)
    
    # Photo count text
    if additional_count > 0:
        photo_text = f"{core_count} (+{additional_count}) photos"
    else:
        photo_text = f"{core_count} photos"
    
    # Date range formatting (same logic as sidebar)
    start_date = suggestion.get('event_start_date')
    end_date = suggestion.get('event_end_date')
    date_text = ""
    
    if start_date:
        try:
            from datetime import datetime
            if isinstance(start_date, str):
                start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            else:
                start_dt = start_date
            
            start_formatted = start_dt.strftime('%d-%m-%y')
            
            if end_date:
                if isinstance(end_date, str):
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                else:
                    end_dt = end_date
                
                # Only show end date if it's different from start date
                if start_dt.date() != end_dt.date():
                    end_formatted = end_dt.strftime('%d-%m-%y')
                    date_text = f"{start_formatted} - {end_formatted}"
                else:
                    date_text = start_formatted
            else:
                date_text = start_formatted
        except (ValueError, AttributeError):
            date_text = ""
    
    # Location
    location = suggestion.get('location') or 'Unknown location'
    
    # Display metadata - ensure all parts are strings and not None
    metadata_parts = [photo_text]
    if date_text:
        metadata_parts.append(date_text)
    if location:
        metadata_parts.append(location)
    
    # Filter out any None values just in case
    metadata_parts = [str(part) for part in metadata_parts if part is not None]
    
    st.caption(" | ".join(metadata_parts))
    st.divider()
    
    # --- Action Buttons ---
    render_album_actions(suggestion)
    st.divider()

    # --- Photo Galleries ---
    st.subheader("Core Photos")
    render_photo_grid(strong_ids, suggestion.get('cover_asset_id'))
    
    if weak_ids:
        st.divider()
        render_weak_asset_selector(weak_ids)


def render_album_actions(suggestion: dict):
    """Renders the main action buttons for an album (Approve, Reject, etc.)."""
    s_id = suggestion['id']
    is_enriching = process_service.is_running(f"enrich_{s_id}") or suggestion['status'] == 'enriching'

    if is_enriching:
        st.info("This album is currently being analyzed by the AI. Please wait.", icon="⏳")
        return

    # Layout for action buttons
    cols = st.columns(4)
    
    # Approve Button - enable for both pending_enrichment and pending statuses
    can_create_album = suggestion['status'] in ['pending', 'pending_enrichment']
    if cols[0].button("✅ Create Album in Immich", type="primary", use_container_width=True, disabled=not can_create_album):
        handle_approve_action(suggestion)

    # Reject Button
    if cols[1].button("❌ Reject Suggestion", use_container_width=True):
        handle_reject_action(s_id)

    # Enrich/Re-enrich Button
    enrich_text = "✨ Re-run AI Analysis" if suggestion['status'] == 'pending' else "✨ Run AI Analysis"
    if cols[2].button(enrich_text, use_container_width=True):
        process_service.start_enrichment(s_id)
        st.toast("Enrichment process started!", icon="✨")
        st.rerun()

    # Back to List Button
    if cols[3].button("⬅️ Back to List", use_container_width=True):
        st.session_state.selected_suggestion_id = None
        st.rerun()


def handle_approve_action(suggestion: dict):
    """Logic for when a user approves a suggestion."""
    with st.spinner("Creating album in Immich... This may take a moment."):
        try:
            strong_assets = json.loads(suggestion.get('strong_asset_ids_json', '[]'))
            final_asset_ids = strong_assets + list(st.session_state.included_weak_assets)
            
            success = immich_service.create_album(
                title=suggestion['vlm_title'],
                asset_ids=final_asset_ids,
                cover_asset_id=suggestion['cover_asset_id'],
                highlight_ids=[] # Highlight logic can be added later
            )
            
            if success:
                db_service.update_suggestion_status(suggestion['id'], 'approved')
                st.success(f"Album '{suggestion['vlm_title']}' created successfully in Immich!")
                st.session_state.selected_suggestion_id = None
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
        st.session_state.selected_suggestion_id = None
        time.sleep(2)
        st.rerun()
    except AppServiceError as e:
        logger.error(f"Service error during suggestion rejection: {e}", exc_info=True)
        st.error(f"An error occurred while rejecting: {e}")


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
                strong_ids = json.loads(suggestion.get('strong_asset_ids_json', '[]'))
                weak_ids = json.loads(suggestion.get('weak_asset_ids_json', '[]'))
                total_photos += len(strong_ids) + len(weak_ids)
                if suggestion.get('vlm_title'):
                    titles.append(suggestion['vlm_title'])
            
            # Show confirmation dialog at the top of the page
            st.error("⚠️ **MERGE CONFIRMATION REQUIRED**")
            
            with st.container():
                st.write(f"**Merging {len(suggestions)} suggestions into 1 album:**")
                
                # Show titles in a more compact format
                title_list = []
                for suggestion in suggestions:
                    title = suggestion.get('vlm_title', 'Untitled')
                    if len(title) > 30:
                        title = title[:27] + "..."
                    title_list.append(title)
                
                st.write("• " + " • ".join(title_list))
                st.write(f"**Total photos:** {total_photos}")
                
                col1, col2, col3 = st.columns([1, 1, 2])
                
                if col1.button("✅ Confirm", type="primary", key=f"{merge_key}_confirm", use_container_width=True):
                    logger.info(f"Merge confirmation button clicked for {suggestion_ids}")
                    st.session_state[f"{merge_key}_confirmed"] = True
                    # Don't rerun here, let it continue to the merge logic
                    
                if col2.button("❌ Cancel", key=f"{merge_key}_cancel", use_container_width=True):
                    st.session_state.suggestions_to_enrich.clear()
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
            st.session_state.suggestions_to_enrich.clear()
            
            # Switch to viewing the merged suggestion
            st.session_state.selected_suggestion_id = merged_id
            st.session_state.view_mode = 'album'
            
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
    
    total_pages = (len(asset_ids) + items_per_page - 1) // items_per_page
    
    # Show pagination controls if needed
    if total_pages > 1:
        st.session_state.setdefault("core_photos_page", 0)
        
        # Pagination info and controls
        col1, col2, col3, col4 = st.columns([1, 1, 2, 1])
        
        with col1:
            if st.button("◀ Previous", key="core_prev", disabled=st.session_state.core_photos_page == 0):
                st.session_state.core_photos_page -= 1
                st.rerun()
        
        with col2:
            if st.button("Next ▶", key="core_next", disabled=st.session_state.core_photos_page == total_pages - 1):
                st.session_state.core_photos_page += 1
                st.rerun()
        
        with col3:
            st.caption(f"Page {st.session_state.core_photos_page + 1} of {total_pages} • {len(asset_ids)} photos")
        
        with col4:
            # Jump to cover photo page if there is one
            if cover_id and cover_id in asset_ids:
                cover_index = asset_ids.index(cover_id)
                cover_page = cover_index // items_per_page
                if cover_page != st.session_state.core_photos_page:
                    if st.button("📷 Cover", key="jump_to_cover", help="Go to cover photo"):
                        st.session_state.core_photos_page = cover_page
                        st.rerun()
        
        # Get items for current page
        start_idx = st.session_state.core_photos_page * items_per_page
        end_idx = min(start_idx + items_per_page, len(asset_ids))
        page_asset_ids = asset_ids[start_idx:end_idx]
        
        st.caption(f"Showing photos {start_idx + 1}-{end_idx}")
    else:
        page_asset_ids = asset_ids
        st.caption(f"All {len(asset_ids)} photos")
    
    # Render grid of photos for current page
    for i in range(0, len(page_asset_ids), num_columns):
        cols = st.columns(num_columns)
        for j, asset_id in enumerate(page_asset_ids[i : i + num_columns]):
            with cols[j]:
                thumb_bytes = get_cached_thumbnail(asset_id)
                if thumb_bytes:
                    caption = "Cover" if asset_id == cover_id else ""
                    
                    try:
                        # Try to display the image
                        st.image(
                            thumb_bytes, 
                            caption=caption, 
                            use_container_width=True,
                        )
                        
                        # Add a small overlay button for clicking
                        if st.button("👁️", key=f"view_{asset_id}", help="View full photo", use_container_width=True):
                            st.session_state.selected_asset_id = asset_id
                            st.session_state.view_mode = 'photo'
                            st.rerun()
                    
                    except Exception as e:
                        # If thumbnail display fails, show error with asset info
                        st.error(f"⚠️ Corrupted thumbnail")
                        st.caption(f"Asset: {asset_id[:8]}...")
                        
                        # Still allow viewing (maybe full image works)
                        if st.button("👁️ Try anyway", key=f"view_{asset_id}", help="Try to view full photo", use_container_width=True):
                            st.session_state.selected_asset_id = asset_id
                            st.session_state.view_mode = 'photo'
                            st.rerun()
                        
                else:
                    st.error("🖼️", help=f"Failed to load thumbnail for asset {asset_id}")
                    # Still allow viewing attempt
                    if st.button("👁️ Try anyway", key=f"view_{asset_id}", help="Try to view full photo", use_container_width=True):
                        st.session_state.selected_asset_id = asset_id
                        st.session_state.view_mode = 'photo'
                        st.rerun()


def render_weak_asset_selector(weak_asset_ids: list[str]):
    """Renders the UI for selecting which 'additional' photos to include."""
    st.subheader(f"Review Additional Photos ({len(weak_asset_ids)})")
    st.info("These photos are related, but further in time or location. Select any you wish to include in the final album.")
    
    # Toggle all checkbox with optimized callback
    def toggle_all_weak_assets():
        if st.session_state.get('select_all_weak', False):
            # Bulk update without triggering individual widget updates
            st.session_state.included_weak_assets.update(weak_asset_ids)
            # Update all individual checkbox states efficiently
            for asset_id in weak_asset_ids:
                st.session_state[f"cb_weak_{asset_id}"] = True
        else:
            st.session_state.included_weak_assets.clear()
            # Clear all individual checkbox states efficiently  
            for asset_id in weak_asset_ids:
                st.session_state[f"cb_weak_{asset_id}"] = False
    
    # Show current selection summary
    total_selected = len(st.session_state.included_weak_assets.intersection(set(weak_asset_ids)))
    
    col1, col2 = st.columns([2, 1])
    with col1:
        st.checkbox("Include all additional photos", key="select_all_weak", on_change=toggle_all_weak_assets)
    with col2:
        st.caption(f"Selected: {total_selected}/{len(weak_asset_ids)}")
    
    # Add pagination for large sets to improve performance
    items_per_page = config.get('ui.thumbnails_per_page', 50)
    total_pages = (len(weak_asset_ids) + items_per_page - 1) // items_per_page
    
    if total_pages > 1:
        st.session_state.setdefault("weak_assets_page", 0)
        
        # Pagination controls
        col1, col2, col3 = st.columns([1, 2, 1])
        with col1:
            if st.button("◀ Previous", disabled=st.session_state.weak_assets_page == 0):
                st.session_state.weak_assets_page -= 1
                st.rerun()
        with col2:
            st.caption(f"Page {st.session_state.weak_assets_page + 1} of {total_pages}")
        with col3:
            if st.button("Next ▶", disabled=st.session_state.weak_assets_page == total_pages - 1):
                st.session_state.weak_assets_page += 1
                st.rerun()
        
        # Get items for current page
        start_idx = st.session_state.weak_assets_page * items_per_page
        end_idx = min(start_idx + items_per_page, len(weak_asset_ids))
        page_asset_ids = weak_asset_ids[start_idx:end_idx]
    else:
        page_asset_ids = weak_asset_ids
    
    # Render grid of checkboxes for individual selection
    num_columns = config.get('ui.gallery_columns', 6)
    for i in range(0, len(page_asset_ids), num_columns):
        cols = st.columns(num_columns)
        for j, asset_id in enumerate(page_asset_ids[i : i + num_columns]):
            with cols[j]:
                thumb_bytes = get_cached_thumbnail(asset_id)
                if thumb_bytes:
                    try:
                        # Display the image
                        st.image(thumb_bytes, use_container_width=True)
                    except Exception as e:
                        st.error("⚠️ Corrupted")
                        st.caption(f"Asset: {asset_id[:8]}...")
                    
                    # View button and Include checkbox in same row
                    view_col, include_col = st.columns(2)
                    with view_col:
                        if st.button("👁️", key=f"weak_view_{asset_id}", help="View full photo"):
                            st.session_state.selected_asset_id = asset_id
                            st.session_state.view_mode = 'photo'
                            st.rerun()
                    
                    with include_col:
                        # Use efficient state lookup
                        checkbox_key = f"cb_weak_{asset_id}"
                        if checkbox_key not in st.session_state:
                            st.session_state[checkbox_key] = asset_id in st.session_state.included_weak_assets
                        
                        if st.checkbox("Include", key=checkbox_key, label_visibility="collapsed"):
                            st.session_state.included_weak_assets.add(asset_id)
                        else:
                            st.session_state.included_weak_assets.discard(asset_id)
                else:
                    st.error("🖼️")
                    st.caption(f"Asset: {asset_id[:8]}...")
                    
                    # Still allow interaction
                    view_col, include_col = st.columns(2)
                    with view_col:
                        if st.button("👁️", key=f"weak_view_{asset_id}", help="Try to view"):
                            st.session_state.selected_asset_id = asset_id
                            st.session_state.view_mode = 'photo'
                            st.rerun()
                    
                    with include_col:
                        # Use efficient state lookup
                        checkbox_key = f"cb_weak_{asset_id}"
                        if checkbox_key not in st.session_state:
                            st.session_state[checkbox_key] = asset_id in st.session_state.included_weak_assets
                        
                        if st.checkbox("Include", key=checkbox_key, label_visibility="collapsed"):
                            st.session_state.included_weak_assets.add(asset_id)
                        else:
                            st.session_state.included_weak_assets.discard(asset_id)

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


def render_photo_view(suggestion: dict):
    """Renders the single photo view for a selected asset."""
    asset_id = st.session_state.selected_asset_id
    
    # Back to album button
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        if st.button("⬅️ Back to Album", use_container_width=True):
            st.session_state.view_mode = 'album'
            st.session_state.selected_asset_id = None
            st.rerun()
    
    with col2:
        st.subheader(f"Photo View - {suggestion.get('vlm_title', 'Album')}")
    
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
                        from PIL import Image
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
                st.session_state.view_mode = 'album'
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
    strong_ids = json.loads(suggestion.get('strong_asset_ids_json', '[]'))
    weak_ids = json.loads(suggestion.get('weak_asset_ids_json', '[]'))
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
    if hasattr(st.session_state, 'merge_intent') and st.session_state.merge_intent:
        logger.info(f"Processing merge intent for {st.session_state.merge_intent}")
        handle_merge_suggestions(st.session_state.merge_intent)
        # Clear the intent after processing
        del st.session_state.merge_intent
        return
    
    # Header with title and stats
    suggestions = db_service.get_pending_suggestions(
        sort_by=st.session_state.sort_by, 
        sort_order=st.session_state.sort_order
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
        if st.button("✨ Enrich Selected", disabled=not st.session_state.suggestions_to_enrich, use_container_width=True):
            for s_id in list(st.session_state.suggestions_to_enrich):
                process_service.start_enrichment(s_id)
            st.session_state.suggestions_to_enrich.clear()
            st.toast("Enrichment process(es) started!", icon="✨")
            st.rerun()
    
    with col2:
        if st.button("🔗 Merge Selected", disabled=len(st.session_state.suggestions_to_enrich) < 2, use_container_width=True):
            # Set merge intent instead of calling handler directly
            st.session_state.merge_intent = list(st.session_state.suggestions_to_enrich)
            st.rerun()
    
    with col3:
        if st.button("Clear Selection", use_container_width=True):
            st.session_state.suggestions_to_enrich.clear()
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
                        st.session_state.selected_suggestion_id = None
                        st.session_state.suggestions_to_enrich.clear()
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
        sort_icon = "🔽" if st.session_state.sort_by == "event_start_date" and st.session_state.sort_order == "desc" else "🔼" if st.session_state.sort_by == "event_start_date" else ""
        if st.button(f"**📅 Date** {sort_icon}", key="sort_date", use_container_width=True):
            if st.session_state.sort_by == "event_start_date":
                st.session_state.sort_order = "asc" if st.session_state.sort_order == "desc" else "desc"
            else:
                st.session_state.sort_by = "event_start_date"
                st.session_state.sort_order = "desc"
            st.rerun()
    
    with header_cols[5]:
        sort_icon = "🔽" if st.session_state.sort_by == "image_count" and st.session_state.sort_order == "desc" else "🔼" if st.session_state.sort_by == "image_count" else ""
        if st.button(f"**📊 Photos** {sort_icon}", key="sort_photos", use_container_width=True):
            if st.session_state.sort_by == "image_count":
                st.session_state.sort_order = "asc" if st.session_state.sort_order == "desc" else "desc"
            else:
                st.session_state.sort_by = "image_count"
                st.session_state.sort_order = "desc"
            st.rerun()
    
    with header_cols[6]:
        st.markdown("**📊 Status**")
    
    with header_cols[7]:
        st.markdown("**⚡ Actions**")
    
    st.markdown("---")
    
    # --- Table Rows ---
    for suggestion in suggestions:
        s_id = suggestion['id']
        is_enriching = process_service.is_running(f"enrich_{s_id}") or suggestion['status'] == 'enriching'
        
        cols = st.columns([0.5, 1, 2, 2, 1.5, 1.5, 1, 1])
        
        # Checkbox
        with cols[0]:
            is_selected = s_id in st.session_state.suggestions_to_enrich
            if st.checkbox("Select", value=is_selected, key=f"table_select_{s_id}", label_visibility="collapsed"):
                if s_id not in st.session_state.suggestions_to_enrich:
                    st.session_state.suggestions_to_enrich.add(s_id)
            else:
                st.session_state.suggestions_to_enrich.discard(s_id)
        
        # Thumbnail
        with cols[1]:
            cover_id = suggestion.get('cover_asset_id')
            if not cover_id:
                strong_ids = json.loads(suggestion.get('strong_asset_ids_json', '[]'))
                cover_id = strong_ids[0] if strong_ids else None
            
            thumb_bytes = get_cached_thumbnail(cover_id)
            if thumb_bytes:
                st.image(thumb_bytes, width=80)
            else:
                st.markdown("🖼️")
        
        # Title
        with cols[2]:
            title = suggestion.get('vlm_title', 'Untitled')
            st.markdown(f"**{title}**")
        
        # Location
        with cols[3]:
            location = suggestion.get('location', 'Unknown location')
            st.text(location)
        
        # Date
        with cols[4]:
            start_date = suggestion.get('event_start_date')
            end_date = suggestion.get('event_end_date')
            date_text = ""
            
            if start_date:
                try:
                    from datetime import datetime
                    if isinstance(start_date, str):
                        start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                    else:
                        start_dt = start_date
                    
                    start_formatted = start_dt.strftime('%d-%m-%y')
                    
                    if end_date:
                        if isinstance(end_date, str):
                            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                        else:
                            end_dt = end_date
                        
                        # Only show end date if different from start
                        if start_dt.date() != end_dt.date():
                            end_formatted = end_dt.strftime('%d-%m-%y')
                            date_text = f"{start_formatted} - {end_formatted}"
                        else:
                            date_text = start_formatted
                    else:
                        date_text = start_formatted
                except (ValueError, AttributeError):
                    date_text = "Unknown"
            else:
                date_text = "Unknown"
            
            st.text(date_text)
        
        # Photo count
        with cols[5]:
            strong_ids = json.loads(suggestion.get('strong_asset_ids_json', '[]'))
            weak_ids = json.loads(suggestion.get('weak_asset_ids_json', '[]'))
            core_count = len(strong_ids)
            additional_count = len(weak_ids)
            
            if additional_count > 0:
                photo_text = f"{core_count} (+{additional_count})"
            else:
                photo_text = str(core_count)
            
            st.text(photo_text)
        
        # Status
        with cols[6]:
            status = suggestion['status']
            status_emoji = {
                'pending_enrichment': '⏳',
                'enriching': '🔄',
                'pending': '✅',
                'enrichment_failed': '❌'
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
    
    # --- Sidebar ---
    with st.sidebar:
        render_suggestion_list()
        st.divider()
        render_scan_controls()

    # --- Main Content Area ---
    selected_id = st.session_state.selected_suggestion_id
    if selected_id is None:
        # If no album is selected, show the pending suggestions table view.
        render_suggestions_table_view()
    else:
        # If an album is selected, fetch its details and render the main view.
        suggestion = db_service.get_suggestion_details(selected_id)
        if suggestion:
            # Check if enrichment process is running and add periodic refresh
            is_enriching = process_service.is_running(f"enrich_{selected_id}") or suggestion['status'] == 'enriching'
            if is_enriching:
                # Auto-refresh every 3 seconds while enrichment is running
                time.sleep(3)
                st.rerun()
            if st.session_state.view_mode == 'photo' and st.session_state.selected_asset_id:
                render_photo_view(suggestion)
            else:
                render_album_view(suggestion)
        else:
            # This can happen if the suggestion was deleted in another session.
            st.error(f"Suggestion with ID {selected_id} not found. It may have been deleted.")
            st.session_state.selected_suggestion_id = None
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