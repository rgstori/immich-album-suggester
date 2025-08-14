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
import logging
from contextlib import contextmanager
from functools import lru_cache
from collections import OrderedDict
import threading
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

# Configure logging to avoid exposing sensitive data
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# LRU Cache for images with size limit
class ImageLRUCache:
    def __init__(self, max_size_mb=100):
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.cache = OrderedDict()
        self.size_bytes = 0
        self.lock = threading.Lock()
    
    def get(self, key):
        with self.lock:
            if key in self.cache:
                # Move to end (most recently used)
                self.cache.move_to_end(key)
                return self.cache[key]
            return None
    
    def put(self, key, value):
        with self.lock:
            if key in self.cache:
                # Update existing - remove old size, add new
                old_size = len(self.cache[key]) if self.cache[key] else 0
                self.size_bytes -= old_size
            
            if value is None:
                # Store None values without size impact
                self.cache[key] = None
                self.cache.move_to_end(key)
                return
            
            value_size = len(value)
            
            # Evict items if necessary
            while self.size_bytes + value_size > self.max_size_bytes and self.cache:
                oldest_key, oldest_value = self.cache.popitem(last=False)
                if oldest_value:
                    self.size_bytes -= len(oldest_value)
            
            # Add new item
            self.cache[key] = value
            self.size_bytes += value_size
            self.cache.move_to_end(key)
    
    def clear(self):
        with self.lock:
            self.cache.clear()
            self.size_bytes = 0
    
    def clear_suggestion(self, suggestion_id):
        """Clear all cached images for a specific suggestion"""
        with self.lock:
            keys_to_remove = [k for k in self.cache.keys() if k.startswith(f"{suggestion_id}_")]
            for key in keys_to_remove:
                value = self.cache.pop(key, None)
                if value:
                    self.size_bytes -= len(value)

@st.cache_resource
def get_image_cache():
    """Returns a singleton instance of the ImageLRUCache."""
    return ImageLRUCache(max_size_mb=50) # 50MB limit

# Get the cache instance. This will be the same object across all reruns.
image_cache = get_image_cache()

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
    # Whitelist of allowed tables and columns for security
    ALLOWED_TABLES = {'suggestions', 'scan_logs'}
    ALLOWED_COLUMNS = {'event_start_date', 'location', 'status', 'created_at', 'vlm_title', 
                       'vlm_description', 'strong_asset_ids_json', 'weak_asset_ids_json', 
                       'cover_asset_id', 'timestamp', 'level', 'message'}
    
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Table '{table_name}' is not in the allowed list")
    if column_name not in ALLOWED_COLUMNS:
        raise ValueError(f"Column '{column_name}' is not in the allowed list")
    
    # Use parameterized query for PRAGMA (SQLite doesn't support ? for table names in PRAGMA)
    # but since we've whitelisted, the f-string is now safe
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row['name'] for row in cursor.fetchall()]
    if column_name not in columns:
        # Safe to use f-string after whitelist validation
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        logger.info(f"Added column '{column_name}' to table '{table_name}'.")

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
    
    # State for managing album loading transitions
    if "album_loading" not in st.session_state:
        st.session_state.album_loading = False
    if "album_loading_id" not in st.session_state:
        st.session_state.album_loading_id = None
    
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
    
    # [NEW] State for auto-refresh polling
    if "last_refresh_time" not in st.session_state:
        st.session_state.last_refresh_time = time.time()
    if "refresh_interval" not in st.session_state:
        st.session_state.refresh_interval = 10  # Default 10 seconds, will adjust dynamically


# [MODIFIED] Now fetches all data required for the new rich display and sorting.
@st.cache_data(show_spinner=False)
def get_pending_suggestions():
    """Fetches all suggestions awaiting user action."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, vlm_title, strong_asset_ids_json, weak_asset_ids_json, event_start_date, location, created_at, status
            FROM suggestions 
            WHERE status IN ('pending', 'pending_enrichment', 'enriching')
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
    # Selective cache invalidation - clear only this suggestion's images
    image_cache.clear_suggestion(suggestion_id)
    # Clear Streamlit's data cache for fresh suggestion list
    get_pending_suggestions.clear()
    get_suggestion_details.clear()

def switch_to_album(suggestion_id: int):
    """Properly switches to a new album with loading state management."""
    # Don't switch if already loading the same album
    if st.session_state.album_loading and st.session_state.album_loading_id == suggestion_id:
        return
    
    # Set loading state first
    st.session_state.album_loading = True
    st.session_state.album_loading_id = suggestion_id
    
    # Clear all album-specific state and caches
    if st.session_state.selected_suggestion_id != suggestion_id:
        # Clear old album's cached images
        if st.session_state.selected_suggestion_id:
            image_cache.clear_suggestion(st.session_state.selected_suggestion_id)
    
    # Reset all album-specific session state
    st.session_state.selected_suggestion_id = suggestion_id
    st.session_state.view_mode = 'album'
    st.session_state.gallery_page = 0
    st.session_state.included_weak_assets = set()
    
    # Clear relevant caches for fresh data
    get_suggestion_details.clear()
    
    # Force immediate rerun to show loading state
    st.rerun()

def delete_suggestion(suggestion_id: int):
    """Permanently deletes a single suggestion from the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM suggestions WHERE id = ?", (suggestion_id,))
        conn.commit()
    # Selective cache invalidation
    image_cache.clear_suggestion(suggestion_id)
    get_pending_suggestions.clear()
    get_suggestion_details.clear()

def clear_all_pending_suggestions():
    """Deletes all suggestions with 'pending' status from the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Fix: Use correct status names
        cursor.execute("DELETE FROM suggestions WHERE status IN ('pending', 'pending_enrichment')")
        conn.commit()
    # Clear all caches when doing bulk operations
    image_cache.clear()
    get_pending_suggestions.clear()
    get_suggestion_details.clear()
    
    # Reset UI state if current suggestion was deleted
    if 'selected_suggestion_id' in st.session_state:
        st.session_state.selected_suggestion_id = None
    if 'suggestions_to_enrich' in st.session_state:
        st.session_state.suggestions_to_enrich.clear()

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
    # Clear all caches when clearing bulk selection
    get_pending_suggestions.clear()
    get_suggestion_details.clear()

def start_enrichment_process(suggestion_id: int):
    """Kicks off the backend enrichment script for a single suggestion ID."""
    # Validate suggestion_id is an integer to prevent injection
    if not isinstance(suggestion_id, int) or suggestion_id <= 0:
        logger.error(f"Invalid suggestion_id type: {type(suggestion_id)}")
        st.error("Invalid suggestion ID")
        return
    
    if st.session_state.enrich_processes.get(suggestion_id) and st.session_state.enrich_processes[suggestion_id].poll() is None:
        st.toast(f"Enrichment for suggestion {suggestion_id} is already running.", icon="⏳")
        return

    # Check if this is the currently viewed album for special handling
    is_current_album = st.session_state.selected_suggestion_id == suggestion_id
    
    if is_current_album:
        st.toast(f"Starting VLM enrichment for current album...", icon="✨")
        # Update the main view immediately to show enriching state
        get_suggestion_details.clear()
    else:
        st.toast(f"Starting VLM enrichment for suggestion {suggestion_id}...", icon="✨")
    
    try:
        # Use the same Docker-friendly approach as the scan process
        main_script_path = os.path.join(os.getcwd(), "app", "main.py")
        
        command = [sys.executable, "-m", "app.main", f"--enrich-id={suggestion_id}"]        
        env = os.environ.copy()
        env['PYTHONPATH'] = os.getcwd() + ":" + env.get('PYTHONPATH', '')
        
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=os.getcwd(),
            universal_newlines=True,
            bufsize=1
        )
        st.session_state.enrich_processes[suggestion_id] = process
        # Selective cache clearing for enrichment start
        get_pending_suggestions.clear()
        # Speed up refresh when enrichment is running
        st.session_state.refresh_interval = 2
        
        logger.info(f"Started enrichment process for suggestion {suggestion_id} with PID: {process.pid}")
        
    except Exception as e:
        logger.error(f"Failed to start enrichment process: {e}")
        st.error(f"Failed to start enrichment process: {str(e)}")

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
    # Validate mode to prevent injection
    if mode not in ['incremental', 'full']:
        logger.error(f"Invalid scan mode: {mode}")
        st.error("Invalid scan mode")
        return
    
    try:
        # In Docker environment, use direct Python execution instead of module execution
        # This is more reliable in containerized environments
        command = [sys.executable, "-m", "app.main", f"--mode={mode}"]        
        logger.info(f"Executing command: {' '.join(command)}")
        
        # Get the current environment to ensure subprocess has access to all packages
        env = os.environ.copy()
        # Ensure Python path includes current directory for imports
        env['PYTHONPATH'] = os.getcwd() + ":" + env.get('PYTHONPATH', '')
        
        # Force unbuffered Python output
        env['PYTHONUNBUFFERED'] = '1'
        
        # Create a log file for debug output
        import tempfile
        debug_log_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.log')
        debug_log_path = debug_log_file.name
        debug_log_file.close()
        
        # Start subprocess with output redirected to both pipe and file
        process = subprocess.Popen(
            f'{" ".join(command)} | tee {debug_log_path}',
            shell=True,
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            env=env,
            cwd=os.getcwd(),
            universal_newlines=True,
            bufsize=0  # Unbuffered for immediate output
        )
        
        # Store debug log path for later cleanup
        st.session_state.debug_log_path = debug_log_path
        st.session_state.scan_process = process
        st.session_state.last_log_id = 0 # Reset log viewer
        # Speed up refresh interval when scan is running
        st.session_state.refresh_interval = 2
        
        # Clear suggestion list cache to prepare for new results
        get_pending_suggestions.clear()
        
        logger.info(f"Started scan process with PID: {process.pid}")
        
    except Exception as e:
        logger.error(f"Failed to start scan process: {e}")
        st.error(f"Failed to start scan process: {str(e)}")
        # Show the command that failed for debugging
        st.code(f"Failed command: {' '.join(command) if 'command' in locals() else 'Unknown'}")
        
        # Show additional debug info
        with st.expander("🐛 Startup Debug Information"):
            st.text(f"Python executable: {sys.executable}")
            st.text(f"Working directory: {os.getcwd()}")
            st.text(f"Main script exists: {os.path.exists(main_script_path) if 'main_script_path' in locals() else 'Unknown'}")
            st.text(f"Exception: {str(e)}")
            
            # Test if Python execution works at all
            if st.button("Test Python Execution"):
                test_script = os.path.join(os.getcwd(), "test_script.py")
                if os.path.exists(test_script):
                    try:
                        test_process = subprocess.run(
                            [sys.executable, test_script],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        st.text(f"Test exit code: {test_process.returncode}")
                        if test_process.stdout:
                            st.text("Test stdout:")
                            st.code(test_process.stdout)
                        if test_process.stderr:
                            st.text("Test stderr:")
                            st.code(test_process.stderr)
                    except Exception as test_e:
                        st.error(f"Test script failed: {test_e}")
                else:
                    st.warning("Test script not found")

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


def get_suggestion_thumbnail(suggestion_id: int, suggestion_data: dict) -> bytes:
    """
    Gets a single thumbnail for display in the suggestion list.
    For stage 1: uses first photo, for stage 2: uses cover photo.
    """
    # Determine which asset to use as thumbnail
    strong_ids = json.loads(suggestion_data.get('strong_asset_ids_json', '[]'))
    cover_id = suggestion_data.get('cover_asset_id')
    
    # Use cover if available (stage 2), otherwise first strong asset (stage 1)
    thumbnail_asset_id = cover_id if cover_id else (strong_ids[0] if strong_ids else None)
    
    if not thumbnail_asset_id:
        return None
    
    # Check cache first
    cache_key = f"{suggestion_id}_{thumbnail_asset_id}"
    cached_content = image_cache.get(cache_key)
    if cached_content is not None:
        return cached_content
    
    # Fetch from API
    try:
        api_key = os.getenv("IMMICH_API_KEY")
        headers = {'x-api-key': api_key}
        api_base = _build_api_base_from_env()
        
        candidate_urls = [
            f"{api_base}/asset/thumbnail/{thumbnail_asset_id}",
            f"{api_base}/assets/{thumbnail_asset_id}/thumbnail",
        ]
        
        for url in candidate_urls:
            try:
                response = requests.get(url, headers=headers, timeout=5)  # Shorter timeout for UI
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                content = response.content
                image_cache.put(cache_key, content)
                return content
            except requests.RequestException:
                continue
                
        # If all failed, cache None to avoid retries
        image_cache.put(cache_key, None)
        return None
        
    except Exception as e:
        logger.warning(f"Failed to fetch thumbnail for suggestion {suggestion_id}: {e}")
        image_cache.put(cache_key, None)
        return None

def fetch_and_cache_all_thumbnails(suggestion_id: int, asset_ids: list):
    """
    Fetches all thumbnails for a given list of asset IDs using the LRU cache.
    
    Returns:
        A dictionary mapping asset_id -> image_bytes.
    """
    api_key = os.getenv("IMMICH_API_KEY")
    headers = {'x-api-key': api_key}
    api_base = _build_api_base_from_env()
    
    result_cache = {}
    
    for asset_id in asset_ids:
        # Create cache key with suggestion context
        cache_key = f"{suggestion_id}_{asset_id}"
        
        # Check if already cached
        cached_content = image_cache.get(cache_key)
        if cached_content is not None:
            result_cache[asset_id] = cached_content
            continue
        
        # Fetch from API
        try:
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
                    logger.warning(f"Failed to fetch thumbnail for asset {asset_id}: {last_exc}")
                else:
                    logger.warning(f"All thumbnail URL variants returned 404 for asset {asset_id}")
                content = None  # Store None to avoid retries
            
            # Cache the result (including None values)
            image_cache.put(cache_key, content)
            result_cache[asset_id] = content
            
        except Exception as e:
            logger.error(f"Unexpected error fetching thumbnail for asset {asset_id}: {e}")
            image_cache.put(cache_key, None)
            result_cache[asset_id] = None
             
    return result_cache

def fetch_and_cache_all_thumbnails_with_progress(suggestion_id: int, asset_ids: list, progress_bar, status_text):
    """
    Enhanced version with progress tracking for UI feedback.
    
    Returns:
        A dictionary mapping asset_id -> image_bytes.
    """
    api_key = os.getenv("IMMICH_API_KEY")
    headers = {'x-api-key': api_key}
    api_base = _build_api_base_from_env()
    
    result_cache = {}
    total_assets = len(asset_ids)
    processed = 0
    cached_count = 0
    
    for asset_id in asset_ids:
        # Create cache key with suggestion context
        cache_key = f"{suggestion_id}_{asset_id}"
        
        # Update progress
        progress_percent = int((processed / total_assets) * 100)
        progress_bar.progress(progress_percent)
        status_text.text(f"Loading thumbnail {processed + 1} of {total_assets} (cached: {cached_count})")
        
        # Check if already cached
        cached_content = image_cache.get(cache_key)
        if cached_content is not None:
            result_cache[asset_id] = cached_content
            cached_count += 1
            processed += 1
            continue
        
        # Fetch from API
        try:
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
                    logger.warning(f"Failed to fetch thumbnail for asset {asset_id}: {last_exc}")
                else:
                    logger.warning(f"All thumbnail URL variants returned 404 for asset {asset_id}")
                content = None  # Store None to avoid retries
            
            # Cache the result (including None values)
            image_cache.put(cache_key, content)
            result_cache[asset_id] = content
            
        except Exception as e:
            logger.error(f"Unexpected error fetching thumbnail for asset {asset_id}: {e}")
            image_cache.put(cache_key, None)
            result_cache[asset_id] = None
        
        processed += 1
    
    # Final progress update
    progress_bar.progress(100)
    status_text.text(f"Loaded {total_assets} thumbnails ({cached_count} from cache)")
    
    return result_cache


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
        # Check subprocess status and show debug info
        scan_proc = st.session_state.get('scan_process')
        if scan_proc:
            poll_result = scan_proc.poll()
            if poll_result is not None:
                if poll_result == 0:
                    st.success(f"✅ Scan completed successfully")
                else:
                    st.error(f"❌ Scan failed with exit code: {poll_result}")
                    
                    # Show subprocess output for debugging
                    try:
                        stdout_output = ""
                        stderr_output = ""
                        
                        if scan_proc.stdout:
                            stdout_output = scan_proc.stdout.read()
                        if scan_proc.stderr:
                            stderr_output = scan_proc.stderr.read()
                        
                        if stdout_output:
                            st.subheader("Process stdout:")
                            st.code(stdout_output, language="text")
                        if stderr_output:
                            st.subheader("Process stderr:")
                            st.code(stderr_output, language="text")
                        
                        if not stdout_output and not stderr_output:
                            st.warning("No process output captured")
                            
                    except Exception as read_error:
                        st.error(f"Failed to read process output: {read_error}")
            else:
                # Process is still running - try to read available output
                try:
                    # Read from debug log file for live output
                    debug_log_path = getattr(st.session_state, 'debug_log_path', None)
                    if debug_log_path and os.path.exists(debug_log_path):
                        try:
                            with open(debug_log_path, 'r') as f:
                                content = f.read().strip()
                                if content:
                                    # Show last few lines of debug output
                                    lines = content.split('\n')
                                    recent_lines = lines[-5:]  # Show last 5 lines
                                    for line in recent_lines:
                                        if line.strip():
                                            st.text(f"Live output: {line.strip()}")
                        except Exception as read_err:
                            st.text(f"Debug log read error: {read_err}")
                    else:
                        st.text("No debug log available")
                except Exception as e:
                    st.text(f"Debug: Live output failed: {e}")
        
        # Fetch all logs from the DB on each render. Fast enough for this purpose.
        with get_db_connection() as conn:
            # Use ORDER BY id DESC and LIMIT to get the most recent logs first
            logs = conn.execute("SELECT level, message FROM scan_logs ORDER BY id DESC LIMIT 100").fetchall()
        
        if not logs and not is_scan_running:
            if scan_proc and scan_proc.poll() is not None:
                exit_code = scan_proc.poll()
                st.warning(f"Process finished (exit code: {exit_code}) but no logs found.")
                
                # Show process output for debugging
                if exit_code != 0:
                    with st.expander("🐛 Process Output (Click to expand)"):
                        try:
                            if scan_proc.stdout:
                                stdout_output = scan_proc.stdout.read()
                                if stdout_output.strip():
                                    st.subheader("stdout:")
                                    st.code(stdout_output, language="text")
                            
                            if scan_proc.stderr:
                                stderr_output = scan_proc.stderr.read() 
                                if stderr_output.strip():
                                    st.subheader("stderr:")
                                    st.code(stderr_output, language="text")
                                    
                        except Exception as read_error:
                            st.error(f"Failed to read process output: {read_error}")
            else:
                st.info("Logs will appear here when a scan is running.")

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
        # Count suggestions before deletion for better feedback
        suggestions_count = len(get_pending_suggestions())
        clear_all_pending_suggestions()
        if suggestions_count > 0:
            st.toast(f"Deleted {suggestions_count} pending suggestions.", icon="🗑️")
        else:
            st.toast("No pending suggestions to delete.", icon="ℹ️")
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
            # Create layout with thumbnail and content
            thumb_col, content_col = st.columns([1, 3])
            
            # --- Thumbnail Preview ---
            with thumb_col:
                thumbnail_data = get_suggestion_thumbnail(suggestion['id'], suggestion)
                if thumbnail_data:
                    st.image(thumbnail_data, use_container_width=True)
                else:
                    st.markdown("📷")  # Fallback icon
            
            # --- Content Area ---
            with content_col:
                # --- Editable Title with Checkbox ---
                if suggestion['status'] == 'pending_enrichment' and not is_enriching:
                    is_checked = suggestion['id'] in st.session_state.suggestions_to_enrich
                    
                    def checkbox_callback(s_id):
                        if s_id in st.session_state.suggestions_to_enrich:
                            st.session_state.suggestions_to_enrich.remove(s_id)
                        else:
                            st.session_state.suggestions_to_enrich.add(s_id)
                    
                    # Editable title for stage 1 with callback
                    def update_title_stage1(suggestion_id):
                        def callback():
                            new_value = st.session_state[f"title_stage1_{suggestion_id}"]
                            with get_db_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute("UPDATE suggestions SET vlm_title = ? WHERE id = ?", 
                                             (new_value, suggestion_id))
                                conn.commit()
                            get_suggestion_details.clear()
                        return callback
                    
                    current_title = suggestion.get('vlm_title', 'Untitled Album')
                    st.text_input(
                        "Title:",
                        value=current_title,
                        key=f"title_stage1_{suggestion['id']}",
                        label_visibility="collapsed",
                        placeholder="Album title...",
                        on_change=update_title_stage1(suggestion['id'])
                    )
                    
                    st.checkbox(
                        "Select for enrichment",
                        value=is_checked,
                        key=f"enrich_cb_{suggestion['id']}",
                        on_change=checkbox_callback,
                        args=(suggestion['id'],)
                    )
                else:
                    # Editable title for stage 2 with callback
                    def update_title_stage2(suggestion_id):
                        def callback():
                            new_value = st.session_state[f"title_stage2_{suggestion_id}"]
                            with get_db_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute("UPDATE suggestions SET vlm_title = ? WHERE id = ?", 
                                             (new_value, suggestion_id))
                                conn.commit()
                            get_suggestion_details.clear()
                        return callback
                    
                    current_title = suggestion.get('vlm_title', 'Untitled Album')
                    st.text_input(
                        "Title:",
                        value=current_title,
                        key=f"title_stage2_{suggestion['id']}",
                        label_visibility="collapsed",
                        placeholder="Album title...",
                        on_change=update_title_stage2(suggestion['id'])
                    )
                
                # --- Enhanced Metadata ---
                # Photo count and date range
                meta_row1_col1, meta_row1_col2 = st.columns(2)
                meta_row1_col1.caption(f"🖼️ {suggestion['total_photos']} photos")
                
                # Calculate date range for better context
                if suggestion['event_date_obj']:
                    date_str = suggestion['event_date_obj'].strftime('%b %d, %Y')
                    meta_row1_col2.caption(f"🗓️ {date_str}")
                else:
                    meta_row1_col2.caption("🗓️ No date")
                
                # Location and status
                if suggestion.get('location'):
                    st.caption(f"📍 {suggestion['location']}")
                
                # Status indicator
                status = suggestion['status']
                is_loading_this_album = (st.session_state.album_loading and 
                                        st.session_state.album_loading_id == suggestion['id'])
                
                if is_loading_this_album:
                    st.caption("🔄 Loading album...")
                elif status == 'pending_enrichment':
                    st.caption("⏳ Ready for AI analysis")
                elif status == 'pending':
                    st.caption("✅ Ready for review")
                elif status == 'enriching' or is_enriching:
                    st.caption("🔄 AI analyzing...")

            # --- Action Buttons (Full Width) ---
            if is_enriching or status == 'enriching':
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
                
                # Disable button if currently loading this album
                is_loading_this = (st.session_state.album_loading and 
                                 st.session_state.album_loading_id == suggestion['id'])
                
                if action_cols[1].button(button_text, key=f"view_btn_{suggestion['id']}", 
                                       use_container_width=True, disabled=is_loading_this):
                    switch_to_album(suggestion['id'])

def render_weak_asset_selector(weak_assets: list, image_cache: dict):
    """Renders the UI for selecting which weak assets to include."""
    # Enhanced header with photo count
    st.subheader(f"Review Additional Photos ({len(weak_assets)} photos)")
    st.info("These photos are related to the event but are further apart in time or location. Review and select any you'd like to include.")
    
    if not weak_assets:
        st.warning("No additional photos found for this album.")
        return

    # Enhanced "Select All" functionality with proper callback
    def toggle_select_all():
        """Callback for select all checkbox to avoid unnecessary reruns."""
        if st.session_state[f"select_all_weak_{st.session_state.selected_suggestion_id}"]:
            # Select all
            st.session_state.included_weak_assets = set(weak_assets)
        else:
            # Deselect all
            st.session_state.included_weak_assets = set()
    
    # Determine if all are currently selected for initial checkbox state
    all_selected = len(st.session_state.included_weak_assets) == len(weak_assets) and \
                   st.session_state.included_weak_assets == set(weak_assets)
    
    st.checkbox(
        f"Include all {len(weak_assets)} additional photos", 
        value=all_selected,
        key=f"select_all_weak_{st.session_state.selected_suggestion_id}",
        on_change=toggle_select_all
    )
    
    # Show selection summary
    selected_count = len(st.session_state.included_weak_assets)
    if selected_count > 0:
        st.info(f"✅ {selected_count} of {len(weak_assets)} additional photos selected for inclusion", icon="📊")
    
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
                    with st.container():
                        st.error("🖼️ Thumbnail failed to load", icon="⚠️")
                        if st.button("Retry", key=f"retry_weak_{asset_id}", size="small"):
                            # Clear cache for this asset and retry
                            cache_key = f"{st.session_state.selected_suggestion_id}_{asset_id}"
                            image_cache.put(cache_key, None)  # Remove from cache
                            st.rerun()
                
                # Enhanced checkbox with callback to avoid unnecessary reruns
                def toggle_asset(asset_id):
                    """Toggle asset inclusion without forcing rerun."""
                    if st.session_state[f"cb_{asset_id}"]:
                        st.session_state.included_weak_assets.add(asset_id)
                    else:
                        st.session_state.included_weak_assets.discard(asset_id)
                
                is_included = asset_id in st.session_state.included_weak_assets
                st.checkbox(
                    "Include", 
                    value=is_included, 
                    key=f"cb_{asset_id}",
                    on_change=toggle_asset,
                    args=(asset_id,)
                )

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
                    with st.container():
                        st.error("🖼️ Thumbnail unavailable", icon="⚠️")
                        if st.button("Retry", key=f"retry_main_{asset_id}", size="small"):
                            # Clear cache for this asset and retry
                            cache_key = f"{st.session_state.selected_suggestion_id}_{asset_id}"
                            image_cache.put(cache_key, None)  # Remove from cache
                            st.rerun() 
                
                # Button to switch to single photo view.
                if st.button("Info", key=f"info_{asset_id}", use_container_width=True):
                    st.session_state.view_mode = "photo"
                    st.session_state.selected_asset_id = asset_id

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

    # --- UNIFIED, NON-BLOCKING PROCESS MONITOR WITH SMART POLLING ---
    # This block runs on every interaction, ensuring the UI state is always
    # eventually consistent without locking the interface.

    # Smart polling: check if we need to auto-refresh
    current_time = time.time()
    time_since_refresh = current_time - st.session_state.last_refresh_time

    # Determine if any processes are running to adjust refresh interval
    scan_running = bool(st.session_state.get('scan_process') and st.session_state.scan_process.poll() is None)
    enrichment_running = any(proc.poll() is None for proc in st.session_state.enrich_processes.values())

    # Adjust refresh interval based on activity
    if scan_running or enrichment_running:
        st.session_state.refresh_interval = 2  # Fast refresh when processes running
    else:
        st.session_state.refresh_interval = 10  # Slower refresh when idle

    # Auto-refresh if enough time has passed
    if time_since_refresh >= st.session_state.refresh_interval:
        st.session_state.last_refresh_time = current_time
        # Clear suggestion cache to get fresh data and trigger rerun
        get_pending_suggestions.clear()
        st.rerun()

    # 1. Check the main clustering scan process
    scan_proc = st.session_state.get('scan_process')
    if scan_proc and scan_proc.poll() is not None:
        st.toast("Scan complete! Updating list...", icon="🎉")
        st.session_state.scan_process = None # Clean up
        st.session_state.refresh_interval = 10  # Reset to slower refresh
        # Clear suggestion list for new scan results
        get_pending_suggestions.clear()
        st.rerun() # Perform a single, final rerun

    # 2. Check all enrichment processes
    for s_id, enrich_proc in list(st.session_state.enrich_processes.items()):
        if enrich_proc.poll() is not None:
            # Process has finished, update its status from 'enriching' if needed
            with get_db_connection() as conn:
                cursor = conn.cursor()
                # If the backend script failed to update status, mark as failed.
                cursor.execute("UPDATE suggestions SET status = 'enrichment_failed' WHERE id = ? AND status = 'enriching'", (s_id,))
                conn.commit()

            del st.session_state.enrich_processes[s_id] # Clean up
            st.toast(f"Enrichment for suggestion {s_id} is complete.", icon="✅")

            # If no more enrichment processes, reset refresh interval
            if not st.session_state.enrich_processes:
                st.session_state.refresh_interval = 10

            # Selective cache clearing for completed enrichment
            get_pending_suggestions.clear()
            get_suggestion_details.clear()
            st.rerun() # Perform a single, final rerun

    # --- Sidebar Composition ---
    with st.sidebar:
        render_suggestion_list()
        st.divider()
        render_scan_controls()
        st.divider()

        # Add a cache clearing button for debugging and updates.
        # This will now correctly clear the @st.cache_resource cache as well.
        if st.button("Clear App Cache"):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.toast("Application caches cleared.", icon="🧹")
            st.rerun()

        # Add debug info button for troubleshooting Docker issues
        if st.button("🐛 Debug Info"):
            st.subheader("Environment Debug Information")
            st.text(f"Python executable: {sys.executable}")
            st.text(f"Working directory: {os.getcwd()}")
            st.text(f"App main.py exists: {os.path.exists(os.path.join(os.getcwd(), 'app', 'main.py'))}")

            # Show current processes
            scan_proc = st.session_state.get('scan_process')
            if scan_proc:
                st.text(f"Scan process PID: {scan_proc.pid}")
                st.text(f"Scan process status: {'running' if scan_proc.poll() is None else 'finished'}")

            # Show environment variables (non-sensitive ones)
            env_vars = ['PYTHONPATH', 'PATH', 'IMMICH_URL']
            for var in env_vars:
                value = os.environ.get(var, 'Not set')
                st.text(f"{var}: {value}")
            st.rerun()

    # --- Main Content Area Composition (RESTRUCTURED) ---

    # Case 1: No suggestion has been selected. Show the welcome screen.
    if st.session_state.selected_suggestion_id is None:
        st.header(cfg_ui.get('welcome_header', "Welcome"))
        st.info(cfg_ui.get('welcome_info', "Select a suggestion or start a scan."))
        st.write("This tool analyzes your Immich photo library to find events, using an AI Vision Language Model to suggest titles and highlights, helping you organize your memories effortlessly.")
        return # Stop execution here.

    # If an album IS selected, fetch its details once.
    suggestion_details = get_suggestion_details(st.session_state.selected_suggestion_id)
    if not suggestion_details:
        st.error("Error: Could not load suggestion details. It might have been deleted.")
        st.session_state.selected_suggestion_id = None
        st.rerun()
        return

    # Case 2: A suggestion is selected. Check for the single photo view FIRST.
    # This is the crucial logic change. By checking for 'photo' mode here, we avoid
    # running any of the heavy album-loading code below if we don't need to.
    if st.session_state.get("view_mode") == "photo":
        # If we're in photo view, render that component and stop.
        render_single_photo_view(config)
        return  # This 'return' prevents the rest of the album view from rendering.

    # Case 3: A suggestion is selected AND we are in the default 'album' view.
    # We can now safely proceed with rendering the full album editor.
    # The complex "album_loading" state management has been removed because the
    # new @st.cache_resource for images makes loading much faster and simpler.

    # --- Start of Album View Rendering ---

    # Album Header with Editable Title
    col_title, col_actions = st.columns([3, 1])
    with col_title:
        # Editable title with callback to prevent interference with button clicks
        def update_album_title():
            new_value = st.session_state[f"album_title_{suggestion_details['id']}"]
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE suggestions SET vlm_title = ? WHERE id = ?",
                                (new_value, suggestion_details['id']))
                conn.commit()
            get_suggestion_details.clear()

        current_title = suggestion_details.get('vlm_title', 'Untitled Album')
        st.text_input(
            "Album Title",
            value=current_title,
            key=f"album_title_{suggestion_details['id']}",
            placeholder="Enter album title...",
            on_change=update_album_title
        )

    with col_actions:
        st.write("")  # Spacer
        if st.button("🔙 Back to List", use_container_width=True, key="back_to_list_header"):
            st.session_state.selected_suggestion_id = None
            st.session_state.view_mode = "album" # Reset view mode when going back
            st.rerun()

    # Enhanced Metadata Section
    st.subheader("📊 Album Details")
    strong_ids = json.loads(suggestion_details.get('strong_asset_ids_json', '[]'))
    weak_ids = json.loads(suggestion_details.get('weak_asset_ids_json', '[]'))
    all_asset_ids = strong_ids + weak_ids

    meta_col1, meta_col2, meta_col3 = st.columns(3)
    with meta_col1:
        st.metric("📷 Photos", len(all_asset_ids))
        st.metric("🎯 Core Photos", len(strong_ids))
    with meta_col2:
        if suggestion_details.get('event_start_date'):
            event_date = pd.to_datetime(suggestion_details['event_start_date'])
            st.metric("📅 Event Date", event_date.strftime('%B %d, %Y'))
            st.metric("🕐 Time", event_date.strftime('%I:%M %p'))
        else:
            st.metric("📅 Event Date", "Unknown")
    with meta_col3:
        st.metric("🌍 Location", suggestion_details.get('location', "Unknown"))
        current_status = suggestion_details.get('status')
        is_enriching_now = (st.session_state.enrich_processes.get(suggestion_details['id']) and
                            st.session_state.enrich_processes[suggestion_details['id']].poll() is None)
        status_display = '🔄 AI Analyzing...' if is_enriching_now or current_status == 'enriching' else {
            'pending_enrichment': '⏳ Ready for AI',
            'pending': '✅ Ready to Create',
            'approved': '✅ Approved',
            'rejected': '❌ Rejected'
        }.get(current_status, f'❓ {current_status}')
        st.metric("📊 Status", status_display)

    # Editable Description
    st.subheader("📝 Description")
    def update_album_description():
        new_value = st.session_state[f"album_description_{suggestion_details['id']}"]
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE suggestions SET vlm_description = ? WHERE id = ?",
                            (new_value, suggestion_details['id']))
            conn.commit()
        get_suggestion_details.clear()
    current_description = suggestion_details.get('vlm_description', '')
    st.text_area(
        "Album Description",
        value=current_description,
        key=f"album_description_{suggestion_details['id']}",
        height=100,
        label_visibility="collapsed",
        on_change=update_album_description
    )

    # Cover Photo Selection
    st.subheader("🖼️ Cover Photo Selection")
    current_cover = suggestion_details.get('cover_asset_id')
    with st.spinner("Loading cover photo options..."):
        # The new caching makes this call very fast after the first load.
        cover_options = fetch_and_cache_all_thumbnails(suggestion_details['id'], strong_ids[:6])
    cover_cols = st.columns(6)
    selected_cover = current_cover
    for idx, asset_id in enumerate(list(cover_options.keys())[:6]):
        with cover_cols[idx]:
            if cover_options[asset_id]:
                st.image(cover_options[asset_id], use_container_width=True)
                if st.button("Set Cover", key=f"cover_{asset_id}", use_container_width=True):
                    selected_cover = asset_id
            else:
                st.error("❌")
    if selected_cover != current_cover:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE suggestions SET cover_asset_id = ? WHERE id = ?",
                            (selected_cover, suggestion_details['id']))
            conn.commit()
        get_suggestion_details.clear()
        st.rerun()

    st.divider()

    # Final Action Buttons
    if is_enriching_now or current_status == 'enriching':
        st.info("🔄 Album is being analyzed by AI. Please wait...", icon="⏳")
    elif current_status == 'pending_enrichment':
        action_col1, _ = st.columns([2, 1])
        with action_col1:
            if st.button("✨ Start AI Enrichment", type="primary", use_container_width=True):
                start_enrichment_process(suggestion_details['id'])
                st.rerun()
    elif current_status == 'pending':
        action_col1, action_col2, action_col3 = st.columns([2, 2, 1])
        with action_col1:
            if st.button("✅ Create Album in Immich", type="primary", use_container_width=True):
                with st.spinner("Creating album in Immich..."):
                    success = trigger_album_creation(suggestion_details, st.session_state.included_weak_assets)
                if success:
                    st.success(f"Album '{suggestion_details['vlm_title']}' created successfully!")
                    update_suggestion_status(st.session_state.selected_suggestion_id, 'approved')
                    st.session_state.selected_suggestion_id = None
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error("Album creation failed. Check the logs for details.")
        with action_col2:
            if st.button("❌ Reject Album", use_container_width=True):
                update_suggestion_status(st.session_state.selected_suggestion_id, 'rejected')
                st.warning(f"Album '{suggestion_details['vlm_title']}' has been rejected.")
                st.session_state.selected_suggestion_id = None
                time.sleep(2)
                st.rerun()
        with action_col3:
            if st.button("🔄 Re-run AI", use_container_width=True, help="Re-analyze with AI"):
                start_enrichment_process(suggestion_details['id'])
                st.rerun()
    else:
        st.warning(f"Album status: {current_status}. No further actions available.")

    st.divider()

    # Photo Galleries
    if all_asset_ids:
        st.subheader("Loading Gallery")
        loading_progress = st.progress(0)
        loading_status = st.empty()
        # This call is now super fast on subsequent renders thanks to @st.cache_resource
        thumbnail_cache = fetch_and_cache_all_thumbnails_with_progress(
            suggestion_details['id'], all_asset_ids, loading_progress, loading_status
        )
        loading_progress.empty()
        loading_status.empty()

        render_album_gallery("Core Album Photos", strong_ids, current_cover, thumbnail_cache, config)

        if weak_ids:
            st.divider()
            render_weak_asset_selector(weak_ids, thumbnail_cache)
    else:
        st.warning("This album has no photos to display.")

if __name__ == "__main__":
    main()