# app/clustering.py
"""
Contains the core two-stage clustering logic.
Stage 1: Groups photos into dense "eventlets" based on time and space.
Stage 2: Merges eventlets into final albums based on visual similarity.
"""
import pandas as pd
import numpy as np
from sklearn.cluster import DBSCAN
import networkx as nx
from scipy.spatial.distance import cosine
import logging

logger = logging.getLogger(__name__)

def _preprocess_data(df: pd.DataFrame) -> pd.DataFrame:
    """Prepares the raw DataFrame for clustering."""
    # Prioritize 'dateTimeOriginal' but fall back to 'fileCreatedAt'.
    df['timestamp'] = pd.to_datetime(df['dateTimeOriginal'].fillna(df['fileCreatedAt']), errors='coerce')
    df.dropna(subset=['timestamp'], inplace=True)
    df['unix_time'] = df['timestamp'].astype(np.int64) // 10**9
    
    # Convert string representation of embedding back to numpy array.
    df['embedding_list'] = df['embedding'].apply(lambda x: np.fromstring(x.strip('[]'), sep=','))
    return df

def find_album_candidates(df: pd.DataFrame, config: dict) -> list[dict]:
    """
    Orchestrates the entire two-stage clustering process.

    Returns:
        A list of dictionaries, where each dictionary represents a potential
        album with its strong/weak assets and metadata.
    """
    if df.empty:
        return []

    df = _preprocess_data(df)
    cfg_s1 = config['clustering']['stage1']
    cfg_s2 = config['clustering']['stage2']

    # --- STAGE 1: DBSCAN to find "Eventlets" ---
    logger.info("Stage 1: Finding dense 'eventlets' using DBSCAN")
    # Separate geotagged and non-geotagged assets for different clustering strategies.
    df_geo = df.dropna(subset=['latitude', 'longitude']).copy()
    features_geo = df_geo[['unix_time', 'latitude', 'longitude']].values
    # Normalize features by their respective windows to give them equal weight.
    features_geo[:, 0] /= cfg_s1['time_window_seconds']
    features_geo[:, 1] /= cfg_s1['space_window_degrees']
    features_geo[:, 2] /= cfg_s1['space_window_degrees']
    
    db_geo = DBSCAN(eps=1.0, min_samples=cfg_s1['min_cluster_size'], n_jobs=-1).fit(features_geo)
    df_geo['eventlet_id'] = [f"geo_{l}" for l in db_geo.labels_]

    df_time = df[df['latitude'].isna()].copy()
    features_time = df_time[['unix_time']].values / cfg_s1['time_window_seconds']
    db_time = DBSCAN(eps=1.0, min_samples=cfg_s1['min_cluster_size'], n_jobs=-1).fit(features_time)
    df_time['eventlet_id'] = [f"time_{l}" for l in db_time.labels_]
    
    # Combine results and filter out noise (label -1)
    df_clustered = pd.concat([df_geo, df_time])
    df_eventlets = df_clustered[~df_clustered['eventlet_id'].str.contains("_-1")].copy()
    
    if df_eventlets.empty:
        logger.info("Stage 1 did not produce any eventlets")
        return []
        
    eventlet_count = len(df_eventlets['eventlet_id'].unique())
    logger.info(f"Stage 1 found {eventlet_count} eventlets")

    # --- STAGE 2: Graph-based merging of Eventlets ---
    logger.info("Stage 2: Merging eventlets using visual similarity")
    # Summarize each eventlet by its average embedding and time window.
    eventlet_summary = df_eventlets.groupby('eventlet_id').agg(
        mean_embedding=('embedding_list', lambda x: np.mean(np.vstack(x), axis=0)),
        min_time=('unix_time', 'min'),
        max_time=('unix_time', 'max')
    )
    
    # Build a graph where nodes are eventlets.
    G = nx.Graph()
    G.add_nodes_from(eventlet_summary.index)
    
    summary_list = eventlet_summary.reset_index().to_dict('records')
    for i in range(len(summary_list)):
        for j in range(i + 1, len(summary_list)):
            e1, e2 = summary_list[i], summary_list[j]
            
            # Time Gate: Don't compare eventlets that are too far apart in time.
            time_gap = max(0, e1['min_time'] - e2['max_time'], e2['min_time'] - e1['max_time'])
            if time_gap > (cfg_s2['merge_time_window_days'] * 86400):
                continue
            
            # Similarity Gate: Add an edge if visually similar.
            similarity = cosine(e1['mean_embedding'], e2['mean_embedding'])
            if similarity < cfg_s2['merge_similarity_threshold']:
                G.add_edge(e1['eventlet_id'], e2['eventlet_id'])

    # Each connected component in the graph is a final album candidate.
    album_components = [list(c) for c in nx.connected_components(G) if len(c) >= cfg_s2['min_eventlets_for_album']]
    logger.info(f"Stage 2 merged eventlets into {len(album_components)} final album candidates")
    
    # --- Final Output Formatting ---
    final_albums = []
    for component_eventlets in album_components:
        component_graph = G.subgraph(component_eventlets)
        
        # Based on Design Decision 1: Identify "bridge" eventlets as weak candidates.
        # An articulation point (or bridge) is a node that, if removed, would split
        # the cluster. This is a great proxy for photos that are structurally
        # less central, which might be due to data quality issues (like timezone
        # errors causing a time gap) or actual transitional moments.
        bridges = set(nx.articulation_points(component_graph))
        
        strong_eventlets = set(component_eventlets) - bridges
        weak_eventlets = bridges
        
        album_df = df_eventlets[df_eventlets['eventlet_id'].isin(component_eventlets)]
        strong_assets = df_eventlets[df_eventlets['eventlet_id'].isin(strong_eventlets)]['assetId'].tolist()
        weak_assets = df_eventlets[df_eventlets['eventlet_id'].isin(weak_eventlets)]['assetId'].tolist()

        # Collect metadata for the final album object.
        gps_df = album_df.dropna(subset=['latitude', 'longitude'])
        gps_coords = list(zip(gps_df['latitude'], gps_df['longitude']))
        
        final_albums.append({
            "strong_asset_ids": strong_assets,
            "weak_asset_ids": weak_assets,
            "min_date": album_df['timestamp'].min(),
            "max_date": album_df['timestamp'].max(),
            "gps_coords": gps_coords,
        })
        
    return final_albums


def find_potential_additions_to_albums(assets_df: pd.DataFrame, existing_albums: list[dict], config: dict) -> dict[str, list[str]]:
    """
    Finds photos that could potentially be added to existing Immich albums.
    
    Args:
        assets_df: DataFrame of all available assets (should NOT include assets already in albums)
        existing_albums: List of existing album dictionaries with metadata
        config: Configuration dictionary with clustering parameters
        
    Returns:
        Dictionary mapping album_id to list of asset_ids that could be added
    """
    if assets_df.empty or not existing_albums:
        return {}
    
    logger.info(f"Finding potential additions for {len(existing_albums)} existing albums from {len(assets_df)} available assets")
    
    # Preprocess the available assets
    processed_df = _preprocess_data(assets_df.copy())
    
    cfg = config.get('clustering', {})
    time_threshold_hours = cfg.get('stage1', {}).get('time_threshold_hours', 6)
    location_threshold_km = cfg.get('stage1', {}).get('location_threshold_km', 1.0)
    similarity_threshold = cfg.get('stage2', {}).get('similarity_threshold', 0.7)
    
    potential_additions = {}
    
    for album in existing_albums:
        album_id = album['album_id']
        album_title = album.get('title', 'Unknown Album')
        
        # Get album's asset IDs to fetch their embeddings/metadata
        existing_asset_ids = set(album.get('asset_ids', []))
        if not existing_asset_ids:
            continue
            
        # Calculate album's characteristics from existing assets
        album_start = album.get('start_date')
        album_end = album.get('end_date')
        album_location = album.get('location')
        
        if not album_start or not album_end:
            continue  # Skip albums without date information
            
        # Convert dates for comparison
        if isinstance(album_start, str):
            from datetime import datetime
            album_start = datetime.fromisoformat(album_start.replace('Z', '+00:00'))
            album_end = datetime.fromisoformat(album_end.replace('Z', '+00:00'))
        
        # Time-based filtering: find assets within reasonable time range
        time_margin_hours = time_threshold_hours * 2  # Allow wider margin for additions
        time_start = album_start - pd.Timedelta(hours=time_margin_hours)
        time_end = album_end + pd.Timedelta(hours=time_margin_hours)
        
        time_candidates = processed_df[
            (processed_df['timestamp'] >= time_start) & 
            (processed_df['timestamp'] <= time_end)
        ]
        
        if time_candidates.empty:
            continue
            
        logger.debug(f"Album '{album_title}': {len(time_candidates)} time candidates (margin: {time_margin_hours}h)")
        
        # Location-based filtering if album has location
        location_candidates = time_candidates
        if album_location and not time_candidates.empty:
            # For simplicity, we'll use a text-based location match for now
            # In a more sophisticated implementation, you'd use GPS coordinates
            if 'city' in time_candidates.columns or 'state' in time_candidates.columns:
                # Basic location filtering - could be enhanced with GPS distance calculation
                location_candidates = time_candidates[
                    time_candidates.apply(lambda row: 
                        album_location.lower() in str(row.get('city', '')).lower() or
                        album_location.lower() in str(row.get('state', '')).lower() or
                        album_location.lower() in str(row.get('country', '')).lower(),
                        axis=1
                    )
                ]
                if location_candidates.empty:
                    # If no exact location matches, fall back to time candidates
                    location_candidates = time_candidates
                    
        # For visual similarity, we'd need to get embeddings of existing album photos
        # This is computationally expensive, so we'll implement a simplified version
        # that focuses on time/location for now
        
        candidate_asset_ids = location_candidates['assetId'].tolist()
        
        if candidate_asset_ids:
            potential_additions[album_id] = candidate_asset_ids
            logger.debug(f"Album '{album_title}': Found {len(candidate_asset_ids)} potential additions")
    
    total_suggestions = sum(len(assets) for assets in potential_additions.values())
    logger.info(f"Found {total_suggestions} potential additions across {len(potential_additions)} albums")
    
    return potential_additions