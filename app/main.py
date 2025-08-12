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
import os
from pathlib import Path
import traceback
import pandas as pd

# Import our application modules using relative paths

from . import immich_db, clustering, vlm, geocoding, immich_api

# --- DATABASE HELPERS for suggestions.db ---


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "suggestions.db"


def init_suggestions_db():
    """Initializes the SQLite database and creates tables if they don't exist."""
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # Fetch all suggestions; handle NULLs per-row
        cursor.execute("SELECT strong_asset_ids_json, weak_asset_ids_json FROM suggestions")
        rows = cursor.fetchall()
        
        processed_ids = set()
        for strong_json, weak_json in rows:
            try:
                strong_ids = json.loads(str(strong_json) if strong_json else '[]')
            except json.JSONDecodeError:
                strong_ids = []
            try:
                weak_ids = json.loads(str(weak_json) if weak_json else '[]')
            except json.JSONDecodeError:
                weak_ids = []
            processed_ids.update(strong_ids)
            processed_ids.update(weak_ids)
        return list(processed_ids)

def store_initial_suggestion(candidate: dict, config: dict) -> int:
    """Stores a candidate with default info, returning the new suggestion's ID."""
    date_str = candidate['min_date'].strftime('%B %Y')
    all_asset_ids = candidate['strong_asset_ids'] + candidate['weak_asset_ids']
    location_str = geocoding.get_primary_location(candidate['gps_coords'], config)
    event_start_datetime = candidate['min_date'].to_pydatetime()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO suggestions (status, created_at, vlm_title, vlm_description, strong_asset_ids_json, weak_asset_ids_json, cover_asset_id, event_start_date, location)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            'pending_enrichment', # NEW: Status indicates it's ready for VLM.
            datetime.now(),
            config['defaults']['title_template'].format(date_str=date_str),
            config['defaults']['description'],
            json.dumps(candidate['strong_asset_ids']),
            json.dumps(candidate['weak_asset_ids']),
            random.choice(all_asset_ids) if all_asset_ids else None,
            event_start_datetime, # Use the converted datetime
            location_str
        ))
        conn.commit()
        return cursor.lastrowid

def get_suggestion_for_enrichment(suggestion_id: int) -> dict:
    """Fetches a single suggestion's data needed for VLM/Geo processing."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, strong_asset_ids_json, weak_asset_ids_json FROM suggestions WHERE id = ?", (suggestion_id,))
        row = cursor.fetchone()
        return dict(row) if row else {}

def update_suggestion_with_analysis(suggestion_id: int, analysis_result: dict):
    """Updates a suggestion with VLM results and sets its status to 'pending' for review."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE suggestions
        SET status = 'pending', vlm_title = ?, vlm_description = ?, cover_asset_id = ?
        WHERE id = ?
        """, (
            analysis_result['vlm_title'],
            analysis_result['vlm_description'],
            analysis_result['cover_asset_id'],
            suggestion_id
        ))
        conn.commit()
    log_to_db("INFO", f"[ID: {suggestion_id}] Successfully enriched and updated suggestion: '{analysis_result['vlm_title']}'")

def get_data_for_enrichment(suggestion_id: int) -> dict:
    """Fetches a suggestion's data needed for VLM/Geo processing."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, strong_asset_ids_json, weak_asset_ids_json, event_start_date, location FROM suggestions WHERE id = ?", (suggestion_id,))
        row = cursor.fetchone()
        return dict(row) if row else {}



def log_to_db(level: str, message: str):
    """Writes a log entry to the SQLite database and flushes to stdout."""
    print(message, flush=True) # Force console flush for real-time logs
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO scan_logs (timestamp, level, message) VALUES (?, ?, ?)", (datetime.now(), level, message))
        conn.commit()

def run_clustering_pass(config: dict, mode: str):
    """PASS 1: Finds and stores new album candidates without AI enrichment."""
    log_to_db("INFO", f"--- Pass 1: Clustering started in '{mode}' mode ---")
    excluded_ids = []
    if mode == 'incremental':
        excluded_ids = get_processed_asset_ids()
        log_to_db("INFO", f"Found {len(excluded_ids)} previously processed assets to exclude.")
    
    pg_conn = immich_db.get_connection()
    assets_df = immich_db.fetch_assets(pg_conn, config, excluded_ids)
    pg_conn.close()

    if assets_df.empty:
        log_to_db("INFO", "Pass 1 complete. No new data to process.")
        return

    album_candidates = clustering.find_album_candidates(assets_df, config)
    if not album_candidates:
        log_to_db("INFO", "Clustering complete. No new suggestions generated.")
        return
        
    log_to_db("INFO", f"Clustering complete. Found {len(album_candidates)} potential albums.")
    
    for candidate in album_candidates:
        store_initial_suggestion(candidate, config)
        
    log_to_db("INFO", f"--- Stored {len(album_candidates)} new candidates. Ready for enrichment. ---")


def run_enrichment_pass(config: dict, suggestion_id: int):
    """PASS 2: Enriches a single, specific album candidate with VLM analysis."""
    log_to_db("PROGRESS", f"--- Pass 2: Enriching suggestion ID: {suggestion_id} ---")

    try:
        candidate_data = get_data_for_enrichment(suggestion_id)
        if not candidate_data:
            log_to_db("ERROR", f"[ID: {suggestion_id}] Suggestion not found in the database.")
            return

        strong_ids = json.loads(candidate_data.get('strong_asset_ids_json', '[]'))
        weak_ids = json.loads(candidate_data.get('weak_asset_ids_json', '[]'))
        all_asset_ids = strong_ids + weak_ids

        # Get Context from the stored data
        date_str = pd.to_datetime(candidate_data['event_start_date']).strftime('%B %Y')
        location_str = candidate_data.get('location')

        # Prepare a default result object
        final_result = {
            "vlm_title": config['defaults']['title_template'].format(date_str=date_str),
            "vlm_description": config['defaults']['description'],
            "cover_asset_id": random.choice(all_asset_ids) if all_asset_ids else None
        }

        # If VLM is enabled, attempt analysis
        if config['vlm']['enabled']:
            api_client = immich_api.get_api_client(config)
            sample_size = config['vlm']['sample_size']
            sample_assets = random.sample(all_asset_ids, min(len(all_asset_ids), sample_size))
            
            vlm_result = vlm.get_vlm_analysis(api_client, sample_assets, date_str, location_str, config)
            
            if vlm_result:
                final_result['vlm_title'] = vlm_result['title']
                final_result['vlm_description'] = vlm_result['description']
                cover_index = vlm_result.get('cover_photo_index')
                if isinstance(cover_index, int) and 0 <= cover_index < len(sample_assets):
                    final_result['cover_asset_id'] = sample_assets[cover_index]
        
        # Update the suggestion in the DB with the final results
        update_suggestion_with_analysis(suggestion_id, final_result)
        log_to_db("INFO", f"--- Enrichment for ID: {suggestion_id} completed successfully. ---")

    except Exception as e:
        log_to_db("ERROR", f"An exception occurred during enrichment for ID {suggestion_id}: {e}")
        log_to_db("ERROR", traceback.format_exc())


def main():
    dotenv.load_dotenv()
    parser = argparse.ArgumentParser(description="Immich Album Suggester Engine")
    parser.add_argument('--mode', type=str, choices=['incremental', 'full'], help="Run clustering scan.")
    parser.add_argument('--enrich-id', type=int, help="Run VLM enrichment on a specific suggestion ID.")
    args = parser.parse_args()

    # Ensure exactly one action is specified
    if not args.mode and not args.enrich_id:
        parser.error("No action requested. Please specify either --mode or --enrich-id.")
    if args.mode and args.enrich_id:
        parser.error("Ambiguous action. Please specify either --mode or --enrich-id, not both.")

    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)

    init_suggestions_db()

    if args.mode:
        run_clustering_pass(config, args.mode)
    elif args.enrich_id:
        run_enrichment_pass(config, args.enrich_id)


if __name__ == "__main__":
    main()