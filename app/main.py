# app/main.py
"""
The main orchestrator for the album suggestion engine.
This script is triggered by the UI to run a scan. It coordinates all other
modules to fetch data, perform clustering, get VLM analysis, and finally
store the suggestions in the local SQLite database for the UI to display.
"""
import argparse
import sys
import sqlite3
import json
import random
import yaml
import dotenv
from datetime import datetime

# Import our application modules using relative paths

from . import immich_db, clustering, vlm, geocoding, immich_api

# --- DATABASE HELPERS for suggestions.db ---


def init_suggestions_db():
    """Initializes the SQLite database and creates tables if they don't exist."""
    with sqlite3.connect("suggestions.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP NOT NULL,
            vlm_title TEXT,
            vlm_description TEXT,
            strong_asset_ids_json TEXT,
            weak_asset_ids_json TEXT,
            cover_asset_id TEXT
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scan_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL
        )""")
        conn.commit()

def get_processed_asset_ids() -> list:
    """Gets all asset IDs that are already in any suggestion."""
    with sqlite3.connect("suggestions.db") as conn:
        cursor = conn.cursor()
        # Ensure the JSON columns are not NULL before processing
        cursor.execute("SELECT strong_asset_ids_json, weak_asset_ids_json FROM suggestions WHERE strong_asset_ids_json IS NOT NULL")
        rows = cursor.fetchall()
        
        processed_ids = set()
        for row in rows:
            strong_ids = json.loads(row or '[]')
            weak_ids = json.loads(row or '[]')
            processed_ids.update(strong_ids)
            processed_ids.update(weak_ids)
        return list(processed_ids)

def store_suggestion(suggestion_data: dict):
    """Stores a finalized album suggestion in the SQLite database."""
    with sqlite3.connect("suggestions.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO suggestions (created_at, vlm_title, vlm_description, strong_asset_ids_json, weak_asset_ids_json, cover_asset_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(),
            suggestion_data['vlm_title'],
            suggestion_data['vlm_description'],
            json.dumps(suggestion_data['strong_asset_ids']),
            json.dumps(suggestion_data['weak_asset_ids']),
            suggestion_data['cover_asset_id']
        ))
        conn.commit()

def log_to_db(level: str, message: str):
    """Writes a log entry to the SQLite database for the UI."""
    print(message) # Also print to console for command-line debugging
    with sqlite3.connect("suggestions.db") as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO scan_logs (timestamp, level, message) VALUES (?, ?, ?)", (datetime.now(), level, message))
        conn.commit()

# --- MAIN ORCHESTRATION ---

def main():
    # Load environment variables from .env file for the backend process
    dotenv.load_dotenv()

    parser = argparse.ArgumentParser(description="Immich Album Suggester Engine")
    parser.add_argument('--mode', type=str, choices=['incremental', 'full'], required=True, help="Scan mode.")
    args = parser.parse_args()

    # 1. Load Configuration
    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)

    init_suggestions_db()
    log_to_db("INFO", f"--- Scan started in '{args.mode}' mode ---")

    # 2. Get Excluded Assets (for incremental mode)
    excluded_ids = []
    if args.mode == 'incremental':
        excluded_ids = get_processed_asset_ids()
        log_to_db("INFO", f"Found {len(excluded_ids)} previously processed assets to exclude.")
    
    # 3. Fetch Data from Immich DB
    pg_conn = immich_db.get_connection()
    assets_df = immich_db.fetch_assets(pg_conn, config, excluded_ids)
    pg_conn.close()

    if assets_df.empty:
        log_to_db("INFO", "Scan complete. No new data to process.")
        return

    # 4. Perform Clustering
    album_candidates = clustering.find_album_candidates(assets_df, config)
    if not album_candidates:
        log_to_db("INFO", "Scan complete. No suggestions generated.")
        return
        
    log_to_db("INFO", f"Clustering complete. Found {len(album_candidates)} potential albums.")
    
    # 5. Process Each Candidate
    api_client = immich_api.get_api_client(config)
    total_candidates = len(album_candidates)
    
    for i, candidate in enumerate(album_candidates):
        log_to_db("PROGRESS", f"Processing candidate {i+1}/{total_candidates}...")
        
        all_asset_ids = candidate['strong_asset_ids'] + candidate['weak_asset_ids']
        
        # Get Context
        date_str = candidate['min_date'].strftime('%B %Y') # e.g., "July 2025"
        location_str = geocoding.get_primary_location(candidate['gps_coords'], config)
        
        # Start with default values for the suggestion.
        suggestion = {
            "vlm_title": config['defaults']['title_template'].format(date_str=date_str),
            "vlm_description": config['defaults']['description'],
            "cover_asset_id": random.choice(all_asset_ids) if all_asset_ids else None,
            **candidate
        }
        
        # If VLM is enabled, attempt to get a more detailed analysis.
        if config['vlm']['enabled']:
            sample_size = config['vlm']['sample_size']
            sample_assets = random.sample(all_asset_ids, min(len(all_asset_ids), sample_size))
            
            try:
                vlm_result = vlm.get_vlm_analysis(api_client, sample_assets, date_str, location_str, config)
                
                if vlm_result:
                    # Override defaults with VLM results if successful.
                    suggestion['vlm_title'] = vlm_result['title']
                    suggestion['vlm_description'] = vlm_result['description']
                    
                    cover_index = vlm_result.get('cover_photo_index')
                    if cover_index is not None and isinstance(cover_index, int) and cover_index < len(sample_assets):
                        suggestion['cover_asset_id'] = sample_assets[cover_index]
            except Exception as e:
                # If VLM fails, log the error to the DB for UI visibility and continue with defaults.
                log_to_db("ERROR", f"VLM analysis failed for candidate {i+1}: {e}")
                        
        # 6. Store final suggestion in DB
        store_suggestion(suggestion)
        log_to_db("INFO", f"Stored suggestion: '{suggestion['vlm_title']}'")

    log_to_db("INFO", "--- Scan successfully completed! ---")

if __name__ == "__main__":
    main()