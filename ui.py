# ui.py (Section 1 Implemented)
"""
The main user interface for the Immich Album Suggester application.
Built with Streamlit, this UI serves as the central command console to:
- Trigger new album suggestion scans (incremental or full).
- Monitor the progress and logs of running scans.
- Review, inspect, and approve pending album suggestions.
"""

import streamlit as st
import sqlite3
import subprocess
import pandas as pd
import time
import json
import math
from contextlib import contextmanager
import yaml
import sys
import os
from pathlib import Path

# Database configuration
DB_PATH = Path('data/suggestions.db')

# Ensure the data directory exists
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# --- Section 1: Data & State Management ---

@contextmanager
def get_db_connection():

    """Provides a safe way to connect to the SQLite database."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row # Return rows as dictionary-like objects
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """
    Initializes the SQLite database. Creates the 'suggestions' and 'scan_logs'
    tables if they do not already exist. This is safe to run on every startup.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Suggestions table stores the output from the clustering engine.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL DEFAULT 'pending', -- pending, approved, rejected
            created_at TIMESTAMP NOT NULL,
            vlm_title TEXT,
            vlm_description TEXT,
            strong_asset_ids_json TEXT,
            weak_asset_ids_json TEXT,
            cover_asset_id TEXT
        )""")
        # Scan logs table stores real-time output from the backend script for the UI.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scan_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL,
            level TEXT NOT NULL, -- INFO, PROGRESS, ERROR
            message TEXT NOT NULL
        )""")
        conn.commit()

def init_session_state():
    """
    Initializes all necessary keys in Streamlit's session state to prevent
    'key not found' errors. This is the central state management for the UI.
    """
    # State for tracking the currently viewed suggestion and its display mode
    if "selected_suggestion_id" not in st.session_state:
        st.session_state.selected_suggestion_id = None
    if "view_mode" not in st.session_state:
        st.session_state.view_mode = "album"  # Can be 'album' or 'photo'
    
    # State for managing the photo gallery
    if "gallery_page" not in st.session_state:
        st.session_state.gallery_page = 0
        
    # State for tracking which weak assets the user wants to include
    if "included_weak_assets" not in st.session_state:
        st.session_state.included_weak_assets = set()
        
    # State for managing the background scan process
    if "scan_process" not in st.session_state:
        st.session_state.scan_process = None # Will hold the subprocess.Popen object

@st.cache_data(show_spinner=False)
def get_pending_suggestions():
    """
    Fetches all suggestions with 'pending' status from the database.
    This is cached to prevent re-querying the DB on every minor UI interaction.
    The cache is invalidated when a suggestion's status changes.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, vlm_title FROM suggestions WHERE status = 'pending' ORDER BY created_at DESC")
        suggestions = [dict(row) for row in cursor.fetchall()]
        return suggestions

@st.cache_data(show_spinner=False)
def get_suggestion_details(suggestion_id: int):
    """
    Fetches all data for a single suggestion by its ID.
    This is cached so that we don't re-query when, for example, changing gallery pages.
    """
    if suggestion_id is None:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,))
        details = cursor.fetchone()
        return dict(details) if details else None

def get_scan_logs(last_id_seen: int):
    """
    Fetches all scan log entries since the last one seen by the UI.
    This enables an efficient, non-blocking live log view.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, level, message FROM scan_logs WHERE id > ? ORDER BY id ASC", (last_id_seen,))
        return [dict(row) for row in cursor.fetchall()]

def update_suggestion_status(suggestion_id: int, status: str):
    """Updates a suggestion's status and clears relevant caches."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE suggestions SET status = ? WHERE id = ?", (status, suggestion_id))
        conn.commit()
    # Invalidate caches to force the UI to get fresh data
    st.cache_data.clear()

def clear_scan_logs():
    """Clears the scan_logs table, typically before starting a new scan."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scan_logs")
        conn.commit()

# --- Section 2: Backend & API Interaction ---
# This section contains functions that perform long-running actions or
# communicate with external services like the Immich API and the backend
# clustering script.

# Import our backend logic. These will be used to create the album.
# We assume these functions are available in the app/ directory.
from app.immich_api import get_api_client, create_immich_album
from app.immich_db import get_connection as get_immich_db_connection # Alias to avoid confusion

def start_scan_process(mode: str):
    """
    Kicks off the backend clustering script ('app/main.py') in a non-blocking
    way using subprocess.Popen. The UI can then monitor its progress via the DB.
    """
    # Prevent concurrent scans as per design decision.
    if st.session_state.get('scan_process') and st.session_state.scan_process.poll() is None:
        st.toast("⚠️ A scan is already in progress.", icon="⚠️")
        return

    st.toast(f"Starting {mode} scan...", icon="🚀")
    
    # Clear old logs before starting a new scan.
    clear_scan_logs()

    # We run the script in a separate process. The UI is now free.
    # We pass the full path to the python executable that streamlit is using
    # to ensure it runs with the same environment and dependencies.
    command = [sys.executable, "-m", "app.main", f"--mode={mode}"]
    process = subprocess.Popen(command, stdout=sys.stdout, stderr=sys.stderr)
    st.session_state.scan_process = process
    st.session_state.last_log_id = 0 # Reset log viewer

    # Invalidate suggestion list cache to prepare for new results
    st.cache_data.clear()

def trigger_album_creation(suggestion: dict, included_weak_assets: set):
    """
    Wrapper function that handles the full album creation lifecycle.
    It gathers asset IDs, connects to the Immich API, and calls the
    backend function to create the album.
    
    Args:
        suggestion: The dictionary of suggestion details from the DB.
        included_weak_assets: A set of weak asset IDs the user chose to include.
    
    Returns:
        True if successful, False otherwise.
    """
    strong_assets = json.loads(suggestion.get('strong_asset_ids_json', '[]'))
    
    final_asset_ids = strong_assets + list(included_weak_assets)
    title = suggestion['vlm_title']
    cover_id = suggestion['cover_asset_id']
    
    # For now, we'll consider all selected assets as highlights.
    # This could be refined later if the VLM provides a reliable highlight list.
    highlight_ids = list(included_weak_assets)
    if cover_id not in highlight_ids:
        highlight_ids.append(cover_id)
        
    try:
        # We need to load the main config to get API keys etc.
        # This assumes config.yaml is in the root.
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)

        # Secrets are loaded from env vars by the SDK/connector
        config['immich']['api_key'] = os.getenv("IMMICH_API_KEY")

        with get_api_client(config) as api_client:
            success = create_immich_album(
                api_client=api_client,
                title=title,
                asset_ids=final_asset_ids,
                cover_asset_id=cover_id,
                highlight_ids=highlight_ids
            )
        return success
    except Exception as e:
        st.error(f"Failed to create album. Error: {e}")
        return False

@st.cache_data(show_spinner=False)
def get_thumbnail_url(asset_id: str) -> str:
    """
    Constructs the full, direct URL for an Immich asset thumbnail.
    This is cached as it will never change for a given asset ID.
    """
    # This assumes the Immich URL is set as an environment variable.
    immich_url = os.getenv("IMMICH_URL", "").rstrip('/')
    return f"{immich_url}/api/asset/thumbnail/{asset_id}"

@st.cache_data(persist=True, show_spinner=False)
def fetch_and_cache_all_thumbnails(_suggestion_id: int, asset_ids: list):
    """
    Fetches all thumbnails for a given list of asset IDs and caches the results.
    The _suggestion_id parameter is a "cache key" - when we switch to a new
    suggestion, this function will re-run.
    
    This is the core of the performant gallery, decorated with @st.cache_data.
    
    Returns:
        A dictionary mapping asset_id -> image_bytes.
    """
    # This function would be too slow without caching.
    st.toast(f"Caching {len(asset_ids)} thumbnails for fast browsing...", icon="🖼️")
    
    # We use a direct requests session for performance.
    api_key = os.getenv("IMMICH_API_KEY")
    headers = {'x-api-key': api_key}
    
    image_cache = {}
    
    # Display a progress bar while caching.
    progress_text = "Caching thumbnails... please wait."
    progress_bar = st.progress(0, text=progress_text)
    
    for i, asset_id in enumerate(asset_ids):
        try:
            url = get_thumbnail_url(asset_id)
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            image_cache[asset_id] = response.content
        except requests.RequestException:
            # If a thumbnail fails, store None so we don't retry.
            image_cache[asset_id] = None
        
        # Update the progress bar.
        progress_bar.progress((i + 1) / len(asset_ids), text=progress_text)
        
    progress_bar.empty() # Remove the progress bar when done.
    return image_cache


# --- Section 3: UI Component Rendering ---
# This section contains a collection of functions, each responsible for rendering
# a specific, self-contained part of the UI. They are the "building blocks"
# that are composed in the final Main Application Layout.

def render_scan_controls():
    """
    Renders the UI for starting scans and monitoring their progress.
    This component is non-blocking and relies on polling the database.
    """
    st.sidebar.subheader("Scan Controls")

    # Disable buttons if a scan is currently running.
    is_scan_running = st.session_state.get('scan_process') and st.session_state.scan_process.poll() is None
    
    col1, col2 = st.sidebar.columns(2)
    if col1.button("Incremental Scan", use_container_width=True, disabled=is_scan_running):
        start_scan_process(mode='incremental')
        st.rerun() # Rerun to immediately show the progress UI

    if col2.button("Full Rescan", use_container_width=True, type="primary", disabled=is_scan_running):
        start_scan_process(mode='full')
        st.rerun()

    # This section is the live monitor, only visible during a scan.
    if is_scan_running:
        with st.sidebar.container(border=True):
            st.info("Scan in progress...")
            progress_bar = st.progress(0, text="Initializing...")
            log_container = st.empty()
            
            # Continuously check for updates until the process completes.
            while st.session_state.scan_process.poll() is None:
                new_logs = get_scan_logs(st.session_state.get('last_log_id', 0))
                if new_logs:
                    for log in new_logs:
                        if log['level'] == 'PROGRESS':
                            # Update progress bar
                            progress_value = int(log['message'])
                            progress_bar.progress(progress_value / 100, text=f"Processing... {progress_value}%")
                        else:
                            # Append to log history
                            st.session_state.log_history += f"{log['message']}\n"
                    
                    st.session_state.last_log_id = new_logs[-1]['id']
                    log_container.text_area("Live Logs", st.session_state.log_history, height=200, key=f"log_area_{time.time()}")
                
                time.sleep(2) # Poll every 2 seconds to avoid overwhelming the DB.
        
        # Scan finished, clean up state.
        st.toast("Scan complete!", icon="🎉")
        st.session_state.scan_process = None
        st.cache_data.clear() # Clear caches to get new suggestion list
        st.rerun() # Rerun to update the UI and hide the progress monitor.


def render_suggestion_list():
    """Renders the list of clickable suggestion buttons in the sidebar."""
    st.sidebar.subheader("Pending Suggestions")
    
    # Use the cached function to get the list.
    suggestions = get_pending_suggestions()
    if not suggestions:
        st.sidebar.info("No pending suggestions. Run a scan!")
        return
        
    for suggestion in suggestions:
        # When a suggestion is clicked, its ID is stored in the session state.
        # This action is the primary trigger for displaying album details.
        if st.sidebar.button(suggestion.get('vlm_title', 'Untitled Album'), key=f"suggestion_btn_{suggestion['id']}", use_container_width=True):
            if st.session_state.selected_suggestion_id != suggestion['id']:
                # Reset states when switching to a NEW suggestion.
                st.session_state.selected_suggestion_id = suggestion['id']
                st.session_state.view_mode = 'album'
                st.session_state.gallery_page = 0
                st.session_state.included_weak_assets = set()
                st.rerun() # Force a rerun to load the new suggestion's details.

def render_weak_asset_selector(weak_assets: list, image_cache: dict):
    """Renders the UI for selecting which weak assets to include."""
    st.subheader("Weakly-Linked Photos")
    st.info("These photos are structurally less central to the event. They might be from travel time between locations or have mismatched metadata. Review and select which ones to include.")
    
    if not weak_assets:
        st.warning("No weak assets found for this album.")
        return

    # "Select All" functionality
    select_all = st.checkbox("Include all weak photos", key=f"select_all_weak_{st.session_state.selected_suggestion_id}")
    if select_all:
        st.session_state.included_weak_assets = set(weak_assets)
    
    # Render the gallery of weak assets with checkboxes.
    for i in range(0, len(weak_assets), 6):
        row_assets = weak_assets[i:i+6]
        cols = st.columns(6)
        for idx, asset_id in enumerate(row_assets):
            with cols[idx]:
                img_bytes = image_cache.get(asset_id)
                if img_bytes:
                    st.image(img_bytes, use_column_width=True)
                else:
                    st.error("X") # Display an error if thumbnail failed to load
                
                # The state of each checkbox is tied to the asset's presence in the set.
                is_included = asset_id in st.session_state.included_weak_assets
                if st.checkbox("Include", value=is_included, key=f"cb_{asset_id}"):
                    st.session_state.included_weak_assets.add(asset_id)
                elif is_included: # This handles the un-checking case
                    st.session_state.included_weak_assets.remove(asset_id)

def render_album_gallery(title: str, asset_ids: list, cover_id: str, image_cache: dict, config: dict):
    """Renders the main paginated gallery for an album's photos."""
    st.subheader(title)
    
    if not asset_ids:
        st.warning("This album contains no photos.")
        return

    cfg_ui = config['ui']
    thumbnails_per_page = cfg_ui['thumbnails_per_page']
    total_pages = math.ceil(len(asset_ids) / thumbnails_per_page)
    
    # Ensure current page is valid, especially if asset list changes.
    # Only need this check once
    if st.session_state.gallery_page >= total_pages:
        st.session_state.gallery_page = 0
        

    # --- Pagination Controls ---
    # Only show if there's more than one page.
    if total_pages > 1:
        page_cols = st.columns([1, 8, 1])
        if page_cols[0].button("< Prev", key="prev_page"):
            st.session_state.gallery_page = max(0, st.session_state.gallery_page - 1)
            st.rerun()
        page_cols[1].markdown(f"<p style='text-align: center;'>Page {st.session_state.gallery_page + 1} of {total_pages}</p>", unsafe_allow_html=True)
        if page_cols[2].button("Next >", key="next_page"):
            st.session_state.gallery_page = min(total_pages - 1, st.session_state.gallery_page + 1)
            st.rerun()

    # --- Thumbnail Grid Display ---
    start_index = st.session_state.gallery_page * thumbnails_per_page
    end_index = start_index + thumbnails_per_page
    page_assets = asset_ids[start_index:end_index]

    # Dynamically create columns for a responsive grid.
    num_columns = cfg_ui['gallery_columns']
    for i in range(0, len(page_assets), num_columns):
        row_assets = page_assets[i:i+num_columns]
        cols = st.columns(num_columns)
        for idx, asset_id in enumerate(row_assets):
            with cols[idx]:
                img_bytes = image_cache.get(asset_id)
                if img_bytes:
                    # Add a visual indicator for the cover photo.
                    st.image(img_bytes, use_column_width=True, caption="Cover" if asset_id == cover_id else "")
                else:
                    st.error("X") # Thumbnail failed to load
                
                # Button to switch to single photo view.
                if st.button("Info", key=f"info_{asset_id}", use_container_width=True):
                    st.session_state.view_mode = "photo"
                    st.session_state.selected_asset_id = asset_id
                    st.rerun()

def render_single_photo_view():
    """Renders a detailed view for one photo with its EXIF data."""
    asset_id = st.session_state.selected_asset_id
    st.title("Photo Details")
    if st.button("⬅️ Back to Album View"):
        st.session_state.view_mode = 'album'
        st.rerun()
        
    # Get the full-resolution image URL (different from thumbnail URL)
    immich_url = os.getenv("IMMICH_URL", "").rstrip('/')
    api_key = os.getenv("IMMICH_API_KEY")
    full_res_url = f"{immich_url}/api/asset/file/{asset_id}"
    
    st.image(full_res_url, headers={'x-api-key': api_key}, use_column_width=True)
    
    # Fetch and display EXIF data
    st.subheader("EXIF Data")
    with st.spinner("Fetching EXIF data..."):
        exif_data = {}
        # In a real implementation, this would connect to the Immich DB
        # and query the 'exif' table for this asset_id.
        # For now, we use a placeholder.
        # exif_data = get_exif_for_asset(asset_id) 
        exif_data = {
            "Camera": "Canon EOS R5",
            "Lens": "RF 24-70mm F2.8L IS USM",
            "Focal Length": "50mm",
            "Shutter Speed": "1/200s",
            "Aperture": "f/2.8",
            "ISO": 400,
            "Timestamp": "2025-07-11 14:30:05"
        }
        st.json(exif_data)

# --- Section 4: Main Application Layout ---
# This is the final composition layer. It orchestrates the rendering of all
# UI components based on the application's current state, which is stored
# in st.session_state.

def main():
    """
    The main function that runs the Streamlit application.
    """
    # Load configuration once at the start
    try:
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        st.error("FATAL: config.yaml not found. The application cannot start.")
        return
    
    cfg_ui = config.get('ui', {})
    
    # Set the page layout and title. This should be the first Streamlit command.
    st.set_page_config(layout="wide", page_title=cfg_ui.get("page_title", "Album Suggester"))
    
    # Ensure the database and session state are initialized on first run.
    init_session_state()
    
    # --- Sidebar Composition ---
    with st.sidebar:
        # The suggestion list is always rendered.
        render_suggestion_list()
        
        st.divider()
        
        # The scan controls are always rendered.
        render_scan_controls()

        st.divider()

        # Add a cache clearing button for debugging and updates.
        if st.button("Clear App Cache"):
            st.cache_data.clear()
            st.toast("Application caches cleared.", icon="🧹")
            st.rerun()

    # --- Main Content Area Composition ---

    # --- Main Content Area Composition ---
    # The content of the main area is determined by the session state.

    # Case 1: No suggestion has been selected yet.
    if st.session_state.selected_suggestion_id is None:
        st.header(cfg_ui.get('welcome_header', "Welcome"))
        st.info(cfg_ui.get('welcome_info', "Select a suggestion or start a scan."))
        st.write("This tool analyzes your Immich photo library to find events, using an AI Vision Language Model to suggest titles and highlights, helping you organize your memories effortlessly.")
        return # Stop execution here for the welcome screen.

    # Case 2: A suggestion IS selected. Fetch its details.

    suggestion_details = get_suggestion_details(st.session_state.selected_suggestion_id)
    if not suggestion_details:
        st.error("Error: Could not load suggestion details. It might have been deleted.")
        st.session_state.selected_suggestion_id = None # Reset state
        return

    # Now, decide which view to show for the selected suggestion.
    if st.session_state.view_mode == 'album':
        # --- Album View ---
        st.title(suggestion_details.get('vlm_title', 'Untitled Album'))
        st.caption(suggestion_details.get('vlm_description', ''))
        
        # --- Action Header ---
        action_cols = st.columns([1, 1, 4])
        if action_cols[0].button("✅ Approve Album", type="primary"):
            with st.spinner("Connecting to Immich and creating album..."):
                success = trigger_album_creation(suggestion_details, st.session_state.included_weak_assets)
            
            if success:
                st.success(f"Album '{suggestion_details['vlm_title']}' created successfully!")
                update_suggestion_status(st.session_state.selected_suggestion_id, 'approved')
                # Reset state to go back to the welcome screen after a delay
                st.session_state.selected_suggestion_id = None
                time.sleep(2)
                st.rerun()
            else:
                st.error("Album creation failed. Check the console logs for details.")
        
        if action_cols[1].button("❌ Reject"):
            update_suggestion_status(st.session_state.selected_suggestion_id, 'rejected')
            st.warning(f"Suggestion '{suggestion_details['vlm_title']}' has been rejected.")
            st.session_state.selected_suggestion_id = None
            time.sleep(2)
            st.rerun()

        st.divider()

        # --- Photo Galleries ---
        strong_assets = json.loads(suggestion_details.get('strong_asset_ids_json', '[]'))
        weak_assets = json.loads(suggestion_details.get('weak_asset_ids_json', '[]'))
        cover_id = suggestion_details.get('cover_asset_id')
        
        # Pre-cache all thumbnails for this suggestion for a smooth experience.
        all_asset_ids = strong_assets + weak_assets
        image_cache = fetch_and_cache_all_thumbnails(suggestion_details['id'], all_asset_ids)

        # Render the selectors and galleries
        render_album_gallery("Album Photos (Strong Candidates)", strong_assets, cover_id, image_cache, config)
        
        if weak_assets:
            st.divider()
            render_weak_asset_selector(weak_assets, image_cache)
            
    elif st.session_state.view_mode == 'photo':
        render_single_photo_view()

# This makes the script runnable.
if __name__ == "__main__":
    init_db() # Initialize DB before running the main app logic
    main()
if __name__ == "__main__":
    main()