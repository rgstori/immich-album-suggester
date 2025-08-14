#!/usr/bin/env python3
# app/main.py
"""
The main orchestrator for the album suggestion engine.

This script is triggered to run a scan or enrichment pass. It coordinates all
other modules and services to fetch data, perform clustering, get VLM analysis,
and finally store the results. It is designed to be a stateless, command-line
driven component of the larger application.
"""

# The config_service MUST be the very first import to ensure logging is
# configured before any other modules attempt to log.
try:
    from app.services import config
except ImportError:
    # This might happen if script is not run as a module. Provide a helpful error.
    import sys
    print("FATAL: Could not import services. Please run this script as a module: `python -m app.main`", file=sys.stderr)
    sys.exit(1)

import argparse
import json
import logging
import random
import sys
import traceback

# Now that logging is configured, we can safely import other modules.
from app.services import db_service, immich_service
from app import clustering, vlm, geocoding

# Initialize the logger for this module.
logger = logging.getLogger(__name__)


def run_clustering_pass(mode: str):
    """
    PASS 1: Finds and stores new album candidates without AI enrichment.
    
    This function orchestrates the process of:
    1. Finding which assets have already been processed.
    2. Fetching new asset data from Immich.
    3. Running the clustering algorithm.
    4. Determining the primary location for new clusters.
    5. Storing the raw results in the suggestions database.
    """
    db_service.log_to_db("INFO", f"--- Pass 1: Clustering started in '{mode}' mode ---")
    
    excluded_ids = []
    if mode == 'incremental':
        excluded_ids = db_service.get_processed_asset_ids()
        db_service.log_to_db("INFO", f"Found {len(excluded_ids)} previously processed assets to exclude.")

    # Use the ImmichService to fetch asset data.
    assets_df = immich_service.fetch_assets_for_clustering(excluded_ids)
    
    if assets_df.empty:
        db_service.log_to_db("INFO", "Pass 1 complete. No new assets found to process.")
        return

    # The clustering logic itself remains in its own module.
    album_candidates = clustering.find_album_candidates(assets_df, config.yaml)
    
    if not album_candidates:
        db_service.log_to_db("INFO", "Clustering complete. No new suggestions were generated.")
        return
        
    db_service.log_to_db("INFO", f"Clustering complete. Found {len(album_candidates)} potential new album(s).")
    
    # Store each new candidate in the database via the DatabaseService.
    for candidate in album_candidates:
        # Geocoding is part of the initial storage step.
        location_str = geocoding.get_primary_location(candidate['gps_coords'])
        db_service.store_initial_suggestion(candidate, location_str)
        
    db_service.log_to_db("INFO", f"--- Stored {len(album_candidates)} new candidate(s). Ready for enrichment. ---")


def run_enrichment_pass(suggestion_id: int):
    """
    PASS 2: Enriches a single, specific album candidate with VLM analysis.
    
    This function orchestrates the process of:
    1. Fetching the candidate's data from our local DB.
    2. Downloading a sample of its images via the ImmichService.
    3. Calling the VLM for analysis.
    4. Updating the candidate in our local DB with the results.
    """
    db_service.log_to_db("PROGRESS", f"--- Pass 2: Enriching suggestion ID: {suggestion_id} ---")

    # The DatabaseService is now responsible for setting the 'enriching' status.
    db_service.update_suggestion_status(suggestion_id, 'enriching')

    # Get all necessary data for the candidate from our local DB.
    candidate_data = db_service.get_suggestion_details(suggestion_id)
    if not candidate_data:
        db_service.log_to_db("ERROR", f"[ID: {suggestion_id}] Suggestion not found in the database.")
        # Mark as failed since we can't proceed.
        db_service.update_suggestion_status(suggestion_id, 'enrichment_failed')
        return

    strong_ids = json.loads(candidate_data.get('strong_asset_ids_json', '[]'))
    weak_ids = json.loads(candidate_data.get('weak_asset_ids_json', '[]'))
    all_asset_ids = strong_ids + weak_ids

    # Prepare a default result object in case VLM is disabled or fails.
    event_date_str = candidate_data['event_start_date'].strftime('%B %Y') if candidate_data.get('event_start_date') else "an unknown date"
    final_result = {
        "vlm_title": config.get('defaults.title_template').format(date_str=event_date_str),
        "vlm_description": config.get('defaults.description'),
        "cover_asset_id": random.choice(all_asset_ids) if all_asset_ids else None
    }

    # If VLM is enabled, attempt the analysis.
    if config.get('vlm.enabled'):
        sample_size = config.get('vlm.sample_size')
        sample_assets = random.sample(all_asset_ids, min(len(all_asset_ids), sample_size))
        
        # The VLM module now uses the ImmichService to get thumbnails.
        # We pass the service itself, not the API client.
        vlm_result = vlm.get_vlm_analysis(
            immich_service=immich_service,
            sample_asset_ids=sample_assets,
            date_str=event_date_str,
            location_str=candidate_data.get('location'),
            config=config.yaml
        )
        
        if vlm_result:
            # If VLM succeeds, update the final result with its output.
            final_result['vlm_title'] = vlm_result.get('title', final_result['vlm_title'])
            final_result['vlm_description'] = vlm_result.get('description', final_result['vlm_description'])
            cover_index = vlm_result.get('cover_photo_index')
            
            if isinstance(cover_index, int) and 0 <= cover_index < len(sample_assets):
                final_result['cover_asset_id'] = sample_assets[cover_index]
        else:
            db_service.log_to_db("WARN", f"[ID: {suggestion_id}] VLM analysis did not return a result. Using defaults.")

    # Update the suggestion in the DB with the final results (either from VLM or default).
    db_service.update_suggestion_with_analysis(suggestion_id, final_result)
    db_service.log_to_db("INFO", f"--- Enrichment for ID: {suggestion_id} completed successfully. ---")


def main():
    """
    Main entry point for the backend script.
    
    Parses arguments and runs the appropriate pass within a top-level error
    handler to ensure all failures are logged.
    """
    parser = argparse.ArgumentParser(description="Immich Album Suggester Engine")
    parser.add_argument('--mode', type=str, choices=['incremental', 'full'], help="Run clustering scan.")
    parser.add_argument('--enrich-id', type=int, help="Run VLM enrichment on a specific suggestion ID.")
    
    try:
        logger.info("=== Album Suggester Engine Starting ===")
        args = parser.parse_args()
        logger.info(f"Arguments parsed: mode={args.mode}, enrich_id={args.enrich_id}")

        # Ensure exactly one action is specified
        if (args.mode and args.enrich_id) or (not args.mode and not args.enrich_id):
            parser.error("Action required: Please specify exactly one of --mode or --enrich-id.")

        # Execute the requested action
        if args.mode:
            logger.info(f"Starting clustering pass in '{args.mode}' mode.")
            run_clustering_pass(args.mode)
        elif args.enrich_id:
            logger.info(f"Starting enrichment for suggestion ID: {args.enrich_id}.")
            run_enrichment_pass(args.enrich_id)
            
        logger.info("=== Album Suggester Engine Finished Successfully ===")

    except Exception as e:
        # This is the master catch-all for any unhandled exception.
        # It ensures that the application logs the failure before exiting.
        # The `exc_info=True` is critical as it includes the full traceback.
        logger.critical(f"FATAL: An unhandled exception occurred in the engine: {e}", exc_info=True)
        
        # Also attempt to log a simplified error to the database for UI visibility.
        try:
            s_id_str = f"Suggestion ID: {args.enrich_id}" if 'args' in locals() and args.enrich_id else "N/A"
            db_service.log_to_db("ERROR", f"A fatal error occurred in the backend. {s_id_str}. See file logs for details.")
            if 'args' in locals() and args.enrich_id:
                # If an enrichment task fails fatally, mark it as such.
                db_service.update_suggestion_status(args.enrich_id, 'enrichment_failed')
        except Exception as db_log_e:
            logger.error(f"Could not write final fatal error to database: {db_log_e}")
        
        sys.exit(1) # Exit with a non-zero status code to indicate failure.


if __name__ == "__main__":
    # This block ensures that when the script is run directly (or with `python -m`),
    # the main function is called.
    main()