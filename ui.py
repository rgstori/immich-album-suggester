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

@st.cache_data(max_entries=500, ttl="1h", show_spinner=False)
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
            return _correct_image_orientation(image_bytes)
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch thumbnail for asset {asset_id} for caching: {e}")
        return None

def _correct_image_orientation(image_bytes: bytes) -> bytes:
    """Reads EXIF data from image bytes and applies necessary rotation."""
    try:
        image = Image.open(BytesIO(image_bytes))
        # This function handles the complex logic of interpreting EXIF orientation tags.
        transposed_image = ImageOps.exif_transpose(image)
        buf = BytesIO()
        # Save back to a new buffer in a standard format.
        transposed_image.convert("RGB").save(buf, format='JPEG')
        return buf.getvalue()
    except Exception:
        # If EXIF parsing fails, return the original bytes to avoid crashing.
        return image_bytes

def switch_to_album_view(suggestion_id: int):
    """
    Callback to cleanly switch the main view to a specific album.
    Resets all relevant session state to ensure the new album displays correctly.
    """
    # Clear state related to the previously viewed album.
    if st.session_state.selected_suggestion_id != suggestion_id:
        st.session_state.gallery_page = 0
        st.session_state.included_weak_assets = set()

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
        log_container = st.container(height=200)
        logs = db_service.get_scan_logs()
        for log in reversed(logs[-50:]): # Show last 50 logs
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
    
    # Fetch suggestions directly from the database service.
    suggestions = db_service.get_pending_suggestions()

    if not suggestions:
        st.sidebar.info("No pending suggestions. Run a scan!")
        return

    # --- Bulk Action Controls ---
    st.sidebar.markdown("---")
    st.sidebar.write("**Bulk Actions**")
    
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

    st.sidebar.markdown("---")

    # --- Render Individual Suggestion Cards ---
    for suggestion in suggestions:
        s_id = suggestion['id']
        is_enriching = process_service.is_running(f"enrich_{s_id}") or suggestion['status'] == 'enriching'

        with st.sidebar.container(border=True):
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

            photo_count = len(json.loads(suggestion.get('strong_asset_ids_json', '[]')))
            st.caption(f"ID: {s_id} | {photo_count} photos")

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
    st.header(f"Reviewing Album: {suggestion.get('vlm_title')}")
    
    strong_ids = json.loads(suggestion.get('strong_asset_ids_json', '[]'))
    weak_ids = json.loads(suggestion.get('weak_asset_ids_json', '[]'))
    
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
    
    # Approve Button
    if cols[0].button("✅ Create Album in Immich", type="primary", use_container_width=True, disabled=suggestion['status'] != 'pending'):
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


def render_photo_grid(asset_ids: list[str], cover_id: str | None):
    """Renders a responsive grid of photo thumbnails."""
    if not asset_ids:
        st.info("No photos to display in this section.")
        return

    # Use a configurable number of columns for the grid layout.
    num_columns = config.get('ui.gallery_columns', 6)
    
    for i in range(0, len(asset_ids), num_columns):
        cols = st.columns(num_columns)
        for j, asset_id in enumerate(asset_ids[i : i + num_columns]):
            with cols[j]:
                thumb_bytes = get_cached_thumbnail(asset_id)
                if thumb_bytes:
                    caption = "Cover" if asset_id == cover_id else ""
                    st.image(thumb_bytes, caption=caption, use_container_width=True)
                else:
                    st.error("🖼️", help=f"Failed to load thumbnail for asset {asset_id}")


def render_weak_asset_selector(weak_asset_ids: list[str]):
    """Renders the UI for selecting which 'additional' photos to include."""
    st.subheader(f"Review Additional Photos ({len(weak_asset_ids)})")
    st.info("These photos are related, but further in time or location. Select any you wish to include in the final album.")
    
    # Toggle all checkbox
    def toggle_all_weak_assets():
        if st.session_state.get('select_all_weak', False):
            st.session_state.included_weak_assets.update(weak_asset_ids)
        else:
            st.session_state.included_weak_assets.clear()
            
    st.checkbox("Include all additional photos", key="select_all_weak", on_change=toggle_all_weak_assets)
    
    # Render grid of checkboxes for individual selection
    num_columns = config.get('ui.gallery_columns', 6)
    for i in range(0, len(weak_asset_ids), num_columns):
        cols = st.columns(num_columns)
        for j, asset_id in enumerate(weak_asset_ids[i : i + num_columns]):
            with cols[j]:
                thumb_bytes = get_cached_thumbnail(asset_id)
                if thumb_bytes:
                    st.image(thumb_bytes, use_container_width=True)
                
                is_included = asset_id in st.session_state.included_weak_assets
                st.checkbox("Include", value=is_included, key=f"cb_weak_{asset_id}", on_change=lambda aid=asset_id: toggle_weak_asset(aid))

def toggle_weak_asset(asset_id: str):
    """Callback to add/remove a single weak asset from the inclusion set."""
    if asset_id in st.session_state.included_weak_assets:
        st.session_state.included_weak_assets.remove(asset_id)
    else:
        st.session_state.included_weak_assets.add(asset_id)

# --- Section 3: Main Application Logic ---

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
        # If no album is selected, show a welcome message.
        st.header(config.get('ui.welcome_header', "Welcome!"))
        st.info(config.get('ui.welcome_info', "Select a suggestion from the sidebar to review it, or start a new scan."))
    else:
        # If an album is selected, fetch its details and render the main view.
        suggestion = db_service.get_suggestion_details(selected_id)
        if suggestion:
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