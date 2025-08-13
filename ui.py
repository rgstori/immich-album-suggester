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
import dotenv
import yaml
import sys
import os
import requests
from app.immich_api import get_api_client, create_immich_album
from app.immich_db import get_connection as get_immich_db_connection, get_exif_for_asset
from pathlib import Path
import pandas as pd
from io import BytesIO
from PIL import Image, ImageOps

# Use a path relative to the script file for robustness
APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "data" / "suggestions.db"

# Load environment variables from the .env file.
dotenv.load_dotenv()

# Ensure the database file's parent directory exists. This will create the `/usr/src/app/data` dir.
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

# [NEW] Helper function for safe, idempotent schema migrations.
def _add_column_if_not_exists(cursor, table_name, column_name, column_type):
    """Checks if a column exists in a table and adds it if it does not."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row['name'] for row in cursor.fetchall()]
    if column_name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        print(f"Added column '{column_name}' to table '{table_name}'.")

# [MODIFIED] init_db now includes a one-time, automatic migration.
def init_db():
    """
    Initializes the SQLite database. Creates tables if they don't exist
    and adds new columns to the suggestions table if they are missing.
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
        
        # [NEW] Add new columns for sorting and display if they don't exist.
        _add_column_if_not_exists(cursor, 'suggestions', 'event_start_date', 'TIMESTAMP')
        _add_column_if_not_exists(cursor, 'suggestions', 'location', 'TEXT')

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

    # [NEW] State for managing suggestion list sorting
    if "suggestion_sort_by" not in st.session_state:
        st.session_state.suggestion_sort_by = "Newest First"
    
    # [NEW] State for managing bulk VLM enrichment
    if "suggestions_to_enrich" not in st.session_state:
        st.session_state.suggestions_to_enrich = set()

    # [NEW] State for tracking multiple enrichment processes
    if "enrich_processes" not in st.session_state:
        st.session_state.enrich_processes = {} # Maps suggestion_id -> subprocess


# [MODIFIED] Now fetches all data required for the new rich display and sorting.
@st.cache_data(show_spinner=False)
def get_pending_suggestions():
    """Fetches all suggestions awaiting user action."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, vlm_title, strong_asset_ids_json, weak_asset_ids_json, event_start_date, location, created_at, status
            FROM suggestions 
            WHERE status IN ('pending', 'pending_enrichment')
            ORDER BY created_at DESC
        """)
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

def delete_suggestion(suggestion_id: int):
    """Permanently deletes a single suggestion from the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM suggestions WHERE id = ?", (suggestion_id,))
        conn.commit()
    st.cache_data.clear()

def clear_all_pending_suggestions():
    """Deletes all suggestions with 'pending' status from the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM suggestions WHERE status IN ('pending', 'pending_vlm')")
        conn.commit()
    st.cache_data.clear()

def clear_scan_logs():
    """Clears the scan_logs table, typically before starting a new scan."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scan_logs")
        conn.commit()

def pre_flight_checks():
    """
    Verifies that the application is correctly configured before launching the UI.
    Checks for config.yaml and required environment variables.
    Returns True if all checks pass, False otherwise.
    """
    # 1. Check for config.yaml
    config_path = APP_DIR / 'config.yaml'
    if not config_path.is_file():
        st.error(
            f"**Configuration Error:** `config.yaml` not found at `{config_path}`."
            "\nPlease ensure the configuration file is in the root directory of the application.",
            icon="🚨"
        )
        return False
    
    # 2. Check for required environment variables
    required_env_vars = [
        "IMMICH_URL",
        "IMMICH_API_KEY",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "DB_HOSTNAME",
        "DB_PORT"
    ]
    
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    
    if missing_vars:
        st.error(
            "**Configuration Error:** The following required environment variables are not set:"
            f"\n\n```\n{', '.join(missing_vars)}\n```\n\n"
            "Please create a `.env` file in the application's root directory or set these "
            "variables in your environment before launching.",
            icon="🚨"
        )
        return False
        
    return True


# --- Section 2: Backend & API Interaction ---
# This section contains functions that perform long-running actions or

# communicate with external services like the Immich API and the backend
# clustering script.

# Import our backend logic. These will be used to create the album.
# We assume these functions are available in the app/ directory.

def clear_all_pending_suggestions():
    """Deletes all suggestions with 'pending' or 'pending_enrichment' status."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Ensure we target both statuses that are considered "pending"
        cursor.execute("DELETE FROM suggestions WHERE status IN ('pending', 'pending_enrichment')")
        conn.commit()
    # Clear session state related to bulk selection
    if 'suggestions_to_enrich' in st.session_state:
        st.session_state.suggestions_to_enrich.clear()
    st.cache_data.clear()

def start_enrichment_process(suggestion_id: int):
    """Kicks off the backend enrichment script for a single suggestion ID."""
    if st.session_state.enrich_processes.get(suggestion_id) and st.session_state.enrich_processes[suggestion_id].poll() is None:
        st.toast(f"Enrichment for suggestion {suggestion_id} is already running.", icon="⏳")
        return

    st.toast(f"Starting VLM enrichment for suggestion {suggestion_id}...", icon="✨")
    command = [sys.executable, "-m", "app.main", f"--enrich-id={suggestion_id}"]
    process = subprocess.Popen(command, stdout=sys.stdout, stderr=sys.stderr)
    st.session_state.enrich_processes[suggestion_id] = process
    st.cache_data.clear() # Clear cache to update status in UI

def _correct_image_orientation(image_bytes: bytes) -> bytes:
    """
    Reads image bytes, checks for an EXIF orientation tag, and applies the
    necessary rotation. Returns the corrected image as bytes.
    """
    try:
        image = Image.open(BytesIO(image_bytes))
        # This function reads the EXIF Orientation tag and rotates/flips the image accordingly
        transposed_image = ImageOps.exif_transpose(image)
        
        # Save the corrected image back to a bytes buffer to be used by Streamlit
        buf = BytesIO()
        transposed_image.save(buf, format='JPEG')
        return buf.getvalue()
    except Exception:
        # If anything goes wrong (e.g., no valid image data), return the original bytes
        return image_bytes

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
        with open(APP_DIR / 'config.yaml', 'r') as f:
            config = yaml.safe_load(f)

        # Manually inject env vars into the config dict for the API client
        config['immich']['url'] = os.getenv("IMMICH_URL")
        config['immich']['api_key'] = os.getenv("IMMICH_API_KEY")

        api_client = get_api_client(config)
        success = create_immich_album(
                api_client=api_client,
                title=title,
                asset_ids=final_asset_ids,

                cover_asset_id=cover_id,
                highlight_ids=highlight_ids
            )
        return success
    except (FileNotFoundError, KeyError, Exception) as e:
        st.error(f"Failed to create album. Error: {e}")
        return False

def _build_api_base_from_env() -> str:
    immich_url = os.getenv("IMMICH_URL", "").strip().rstrip('/')
    if immich_url.lower().endswith('/api'):
        immich_url = immich_url[:-4].rstrip('/')
    return f"{immich_url}/api"


@st.cache_data(persist=True, show_spinner=False)
def fetch_and_cache_all_thumbnails(_suggestion_id: int, asset_ids: list):
    """
    Fetches all thumbnails for a given list of asset IDs and caches the results.
    The _suggestion_id parameter is a "cache key" - when we switch to a new
    suggestion, this function will re-run.
    
    This is a pure data-fetching function, safe for caching. It contains no
    Streamlit UI elements.
    
    Returns:
        A dictionary mapping asset_id -> image_bytes.
    """
    # We use a direct requests session for performance.
    api_key = os.getenv("IMMICH_API_KEY")
    headers = {'x-api-key': api_key}
    api_base = _build_api_base_from_env()
    
    image_cache = {}
    
    for asset_id in asset_ids:
        try:
            # Try both URL patterns; stop at the first that works
            candidate_urls = [
                f"{api_base}/asset/thumbnail/{asset_id}",   # singular
                f"{api_base}/assets/{asset_id}/thumbnail",  # plural
            ]
            content = None
            last_exc = None
            for url in candidate_urls:
                try:
                    response = requests.get(url, headers=headers, timeout=10)
                    if response.status_code == 404:
                        continue
                    response.raise_for_status()
                    content = response.content
                    break
                except requests.RequestException as e:
                    last_exc = e
                    if not (hasattr(e, 'response') and e.response is not None and e.response.status_code == 404):
                        break
            if content is None:
                if last_exc:
                    raise last_exc
                else:
                    raise requests.RequestException("All thumbnail URL variants returned 404.")
            image_cache[asset_id] = content
        except requests.RequestException:
             # If a thumbnail fails, store None so we don't retry.
             image_cache[asset_id] = None
             
    return image_cache


# --- Section 3: UI Component Rendering ---
# This section contains a collection of functions, each responsible for rendering
# a specific, self-contained part of the UI. They are the "building blocks"
# that are composed in the final Main Application Layout.

def render_scan_controls():
    """Renders UI for starting scans and monitoring progress without locking."""
    st.sidebar.subheader("Scan Controls")

    is_scan_running = bool(st.session_state.get('scan_process'))
    
    col1, col2 = st.sidebar.columns(2)
    
    # --- FIX: Replaced '...' with the correct keyword arguments ---
    if col1.button("Incremental Scan", use_container_width=True, disabled=is_scan_running):
        start_scan_process('incremental')
        st.rerun()
    
    # --- FIX: Replaced '...' with the correct keyword arguments ---
    if col2.button("Full Rescan", use_container_width=True, type="primary", disabled=is_scan_running):
        start_scan_process('full')
        st.rerun()

    # Display status message if a scan is running
    if is_scan_running:
        st.sidebar.info("Clustering scan in progress...", icon="⏳")
    
    # Display logs (this is now passive and doesn't force reruns)
    log_container = st.sidebar.container(border=True, height=250) # Added a fixed height for better layout
    with log_container:
        # Fetch all logs from the DB on each render. Fast enough for this purpose.
        with get_db_connection() as conn:
            # Use ORDER BY id DESC and LIMIT to get the most recent logs first
            logs = conn.execute("SELECT level, message FROM scan_logs ORDER BY id DESC LIMIT 100").fetchall()
        
        if not logs and not is_scan_running:
            log_container.info("Logs will appear here when a scan is running.")

        # Reverse the list so the most recent log is at the bottom
        for log in reversed(logs):
            if log['level'] == 'ERROR':
                st.error(f"🚨 {log['message']}", icon="🚨")
            else: # INFO, PROGRESS, etc.
                st.write(f"⏳ {log['message']}")

def render_suggestion_list():
    """
    Renders a sortable, informative list of pending suggestions with full
    controls for enrichment, viewing, and bulk management.
    """
    st.sidebar.subheader("Pending Suggestions")
    
    suggestions = get_pending_suggestions()

    if not suggestions:
        st.sidebar.info("No pending suggestions. Run a scan!")
        return

    # --- Sorting Controls ---
    sort_option = st.sidebar.radio(
        "Sort by",
        ("Newest First", "Photo Count", "Event Date"),
        key="suggestion_sort_by",
        horizontal=True,
    )

    # --- Bulk Action Controls ---
    st.sidebar.markdown("---") # Visual separator
    st.sidebar.write("**Bulk Actions**")
    
    col1, col2 = st.sidebar.columns(2)
    with col1:
        if st.button("✨ Enrich Selected", use_container_width=True, disabled=not st.session_state.suggestions_to_enrich):
            for s_id in list(st.session_state.suggestions_to_enrich):
                start_enrichment_process(s_id)
            st.session_state.suggestions_to_enrich.clear()
            st.rerun()

    with col2:
        # Changed "Clear" to "Clear Selection" for clarity
        if st.button("Clear Selection", use_container_width=True, disabled=not st.session_state.suggestions_to_enrich):
            st.session_state.suggestions_to_enrich.clear()
            st.rerun()

    # Use a more dangerous-looking button for a destructive action
    if st.sidebar.button("🗑️ Delete All Pending", use_container_width=True, type="primary"):
        clear_all_pending_suggestions()
        st.toast("All pending suggestions have been deleted.", icon="🗑️")
        st.rerun()

    st.sidebar.markdown("---")

    # --- Suggestion List Processing and Rendering ---
    processed_suggestions = []
    for s in suggestions:
        strong_count = len(json.loads(s.get('strong_asset_ids_json', '[]')))
        weak_count = len(json.loads(s.get('weak_asset_ids_json', '[]')))
        total_photos = strong_count + weak_count
        event_date = pd.to_datetime(s['event_start_date']) if s.get('event_start_date') else None
        processed_suggestions.append({**s, 'total_photos': total_photos, 'event_date_obj': event_date})

    if sort_option == "Photo Count":
        processed_suggestions.sort(key=lambda x: x['total_photos'], reverse=True)
    elif sort_option == "Event Date":
        processed_suggestions.sort(key=lambda x: x['event_date_obj'] if x['event_date_obj'] else pd.Timestamp.min, reverse=True)

    for suggestion in processed_suggestions:
        is_enriching = st.session_state.enrich_processes.get(suggestion['id']) and st.session_state.enrich_processes[suggestion['id']].poll() is None
        
        with st.sidebar.container(border=True):
            # --- Title and Checkbox ---
            if suggestion['status'] == 'pending_enrichment' and not is_enriching:
                is_checked = suggestion['id'] in st.session_state.suggestions_to_enrich
                
                # Use a more robust callback to handle state changes
                def checkbox_callback(s_id):
                    if s_id in st.session_state.suggestions_to_enrich:
                        st.session_state.suggestions_to_enrich.remove(s_id)
                    else:
                        st.session_state.suggestions_to_enrich.add(s_id)
                
                st.checkbox(
                    f"**{suggestion.get('vlm_title', 'Untitled Album')}**",
                    value=is_checked,
                    key=f"enrich_cb_{suggestion['id']}",
                    on_change=checkbox_callback,
                    args=(suggestion['id'],)
                )
            else:
                 st.markdown(f"**{suggestion.get('vlm_title', 'Untitled Album')}**")
            
            # --- Metadata Display ---
            meta_col1, meta_col2 = st.columns(2)
            meta_col1.markdown(f"🖼️ &nbsp; {suggestion['total_photos']} photos")
            date_str = suggestion['event_date_obj'].strftime('%b %Y') if suggestion['event_date_obj'] else "No Date"
            meta_col2.markdown(f"🗓️ &nbsp; {date_str}")
            if suggestion.get('location'):
                st.caption(f"📍 {suggestion['location']}")

            # --- Action Buttons ---
            if is_enriching:
                st.info("Enriching with AI...", icon="⏳")
            else:
                action_cols = st.columns(2)
                
                # Column 1: Enrich button (conditional)
                if suggestion['status'] == 'pending_enrichment':
                    if action_cols[0].button("✨ Enrich", key=f"enrich_btn_{suggestion['id']}", use_container_width=True):
                        start_enrichment_process(suggestion['id'])
                        st.rerun()
                
                # Column 2: View/Review button (always available)
                button_text = "✅ Review" if suggestion['status'] == 'pending' else "View Photos"
                if action_cols[1].button(button_text, key=f"view_btn_{suggestion['id']}", use_container_width=True):
                    # This logic opens the album view for ANY suggestion
                    if st.session_state.selected_suggestion_id != suggestion['id']:
                        st.session_state.selected_suggestion_id = suggestion['id']
                        st.session_state.view_mode = 'album'
                        st.session_state.gallery_page = 0
                        st.session_state.included_weak_assets = set()
                        st.rerun()

def render_weak_asset_selector(weak_assets: list, image_cache: dict):
    """Renders the UI for selecting which weak assets to include."""
    st.subheader("Review Additional Photos")
    st.info("These photos are related to the event but are further apart in time or location. Review and select any you'd like to include.")
    
    if not weak_assets:
        st.warning("No additional photos found for this album.")
        return

    # "Select All" functionality
    select_all = st.checkbox("Include all additional photos", key=f"select_all_weak_{st.session_state.selected_suggestion_id}")
    if select_all:
        st.session_state.included_weak_assets = set(weak_assets)
    else:
        # If unchecked, make sure the set reflects this (for subsequent interactions)
        # This requires a more complex state handling if we want to preserve partial selections
        # For now, we assume unchecking clears the selection made by "Select All"
        pass
    
    # Render the gallery of weak assets with checkboxes.
    for i in range(0, len(weak_assets), 6):
        row_assets = weak_assets[i:i+6]
        cols = st.columns(6)
        for idx, asset_id in enumerate(row_assets):
            with cols[idx]:
                img_bytes = image_cache.get(asset_id)
                if img_bytes:
                    st.image(img_bytes, use_container_width=True)
                else:
                    st.error("X") # Display an error if thumbnail failed to load
                
                # The state of each checkbox is tied to the asset's presence in the set.
                is_included = asset_id in st.session_state.included_weak_assets
                # The callback ensures state is updated immediately on click
                if st.checkbox("Include", value=is_included, key=f"cb_{asset_id}"):
                    if not is_included:
                        st.session_state.included_weak_assets.add(asset_id)
                        st.rerun()
                elif is_included: # This handles the un-checking case
                    st.session_state.included_weak_assets.remove(asset_id)
                    st.rerun()

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
            if st.session_state.gallery_page > 0:
                st.session_state.gallery_page -= 1
                st.rerun()
        page_cols[1].markdown(f"<p style='text-align: center;'>Page {st.session_state.gallery_page + 1} of {total_pages}</p>", unsafe_allow_html=True)
        if page_cols[2].button("Next >", key="next_page"):
            if st.session_state.gallery_page < total_pages - 1:
                st.session_state.gallery_page += 1
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
                    st.image(img_bytes, use_container_width=True, caption="Cover" if asset_id == cover_id else "")
                else:
                    st.error("X") 
                
                # Button to switch to single photo view.
                if st.button("Info", key=f"info_{asset_id}", use_container_width=True):
                    st.session_state.view_mode = "photo"
                    st.session_state.selected_asset_id = asset_id
                    st.rerun()

def render_single_photo_view(config: dict):
    """
    Renders a detailed view for one photo with its EXIF data in a side-by-side layout.
    """
    asset_id = st.session_state.selected_asset_id
    st.title("Photo Details")
    if st.button("⬅️ Back to Album View"):
        st.session_state.view_mode = 'album'
        st.rerun()

    # Create two columns: 2/3 for the image, 1/3 for the data
    col1, col2 = st.columns([2, 1])

    with col1:
        # Fetch and display the full-resolution, oriented image
        api_key = os.getenv("IMMICH_API_KEY")
        api_base = _build_api_base_from_env()
        full_res_url = f"{api_base}/assets/{asset_id}/original"
        headers = {'x-api-key': api_key}

        try:
            response = requests.get(full_res_url, headers=headers, timeout=15)
            response.raise_for_status()
            
            # Apply orientation correction before displaying
            corrected_content = _correct_image_orientation(response.content)
            st.image(corrected_content, use_container_width=True)

        except requests.exceptions.RequestException as e:
            st.error(f"Failed to load full-resolution image: {e}")

    with col2:
        # Fetch and display EXIF data as a clean table
        st.subheader("EXIF Data")
        with st.spinner("Fetching data..."):
            exif_data = get_exif_for_asset(config, asset_id)
        
        if exif_data:
            # Convert the dictionary to a more readable list of tuples
            # And filter out some less useful fields if desired
            filtered_exif = {k: v for k, v in exif_data.items() if v is not None}
            
            # Convert to a pandas DataFrame for clean table display
            df = pd.DataFrame(filtered_exif.items(), columns=['Field', 'Value'])
            df['Value'] = df['Value'].astype(str)
            st.dataframe(df, hide_index=True, use_container_width=True)
        else:
            st.info("No EXIF data available for this asset.")

# --- Section 4: Main Application Layout ---
# This is the final composition layer. It orchestrates the rendering of all
# UI components based on the application's current state, which is stored
# in st.session_state.

def main():
    """
    The main function that runs the Streamlit application.
    """
    st.set_page_config(layout="wide", page_title="Immich Album Suggester")

    # Run pre-flight checks first. If they fail, stop execution.
    if not pre_flight_checks():
        return

    # Load configuration once at the start
    try:
        with open(APP_DIR / 'config.yaml', 'r') as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        st.error("FATAL: config.yaml not found. The application cannot start.")
        return

    cfg_ui = config.get('ui', {})
    
    # Initialize DB and session state right after config load
    init_db()
    init_session_state()

    # --- [NEW] UNIFIED, NON-BLOCKING PROCESS MONITOR ---
    # This block runs on EVERY interaction, ensuring the UI state is always
    # eventually consistent without locking the interface.

    # 1. Check the main clustering scan process
    scan_proc = st.session_state.get('scan_process')
    if scan_proc and scan_proc.poll() is not None:
        st.toast("Scan complete! Updating list...", icon="🎉")
        st.session_state.scan_process = None # Clean up
        st.cache_data.clear()
        st.rerun() # Perform a single, final rerun

    # 2. Check all enrichment processes
    for s_id, enrich_proc in list(st.session_state.enrich_processes.items()):
        if enrich_proc.poll() is not None:
            # Process has finished, update its status from 'enriching' if needed
            with get_db_connection() as conn:
                # If the backend script failed to update status, mark as failed.
                conn.execute("UPDATE suggestions SET status = 'enrichment_failed' WHERE id = ? AND status = 'enriching'", (s_id,))
                conn.commit()
            
            del st.session_state.enrich_processes[s_id] # Clean up
            st.toast(f"Enrichment for suggestion {s_id} is complete.", icon="✅")
            st.cache_data.clear()
            st.rerun() # Perform a single, final rerun

    # --- Sidebar Composition ---
    with st.sidebar:

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
        st.session_state.selected_suggestion_id = None
        st.rerun()
        return

    # Now, decide which view to show for the selected suggestion.
    if st.session_state.view_mode == 'album':
        # --- Album View ---
        st.title(suggestion_details.get('vlm_title', 'Untitled Album'))
        st.caption(suggestion_details.get('vlm_description', ''))
        
        action_cols = st.columns([1.5, 1, 5])
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
        
        # Use a spinner to provide UI feedback *outside* the cached function.
        # This will only be visible on the first load for this suggestion.
        # On subsequent reruns (like changing pages), it will be instant.
        with st.spinner("Preparing image gallery... 🖼️"):
            image_cache = fetch_and_cache_all_thumbnails(suggestion_details['id'], all_asset_ids)

        render_album_gallery("Core Album Photos", strong_assets, cover_id, image_cache, config)
                
        if weak_assets:
            st.divider()
            render_weak_asset_selector(weak_assets, image_cache)
            
    elif st.session_state.view_mode == 'photo':
        render_single_photo_view(config)


if __name__ == "__main__":
    main()