# app/immich_db.py
"""
Handles all direct communication with the Immich PostgreSQL database.
Its sole responsibility is to fetch raw asset data, including metadata
and CLIP embeddings, for the clustering engine.
"""

import os
import sys
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

def get_connection():
    """
    Establishes and returns a connection to the Immich PostgreSQL database
    using credentials from environment variables.
    """
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("POSTGRES_DB"),
            user=os.getenv("POSTGRES_USER"),
            password=os.getenv("POSTGRES_PASSWORD"),
            host=os.getenv("DB_HOSTNAME"),
            port=os.getenv("DB_PORT"),
            cursor_factory=RealDictCursor # Return rows as dictionaries
        )
        return conn
    except psycopg2.OperationalError as e:
        print(f"FATAL: Could not connect to the Immich database. Error: {e}", file=sys.stderr)
        sys.exit(1)

def fetch_assets(conn, config: dict, excluded_asset_ids: list) -> pd.DataFrame:
    """
    Fetches all non-deleted assets from the Immich database, joining with
    EXIF and smart search tables to get all necessary data for clustering.

    Args:
        conn: An active psycopg2 database connection.
        config: The application configuration dictionary.
        excluded_asset_ids: A list of asset IDs to exclude from the query.

    Returns:
        A pandas DataFrame containing all necessary asset information.
    """
    print("  - [DB] Fetching asset data from Immich database...")
    
    # The main query to gather all data in one pass.
    # LEFT JOIN is used for EXIF to include assets that may not have EXIF data.
    # An INNER JOIN is used for smart_search, as assets without an embedding
    # cannot be processed by our clustering logic anyway.
    query = """
    SELECT
        a.id as "assetId",
        a."fileCreatedAt",
        ae."dateTimeOriginal",
        ae.latitude,
        ae.longitude,
        s.embedding
    FROM
        public.assets a
    JOIN
        public.smart_search s ON a.id = s."assetId"
    LEFT JOIN
        public.exif ae ON a.id = ae."assetId"
    WHERE
        a."isArchived" = false AND
        a."deletedAt" IS NULL
    """

    params = []
    if excluded_asset_ids:
        query += " AND a.id NOT IN %s"
        params.append(tuple(excluded_asset_ids))

    # Apply a limit for development/testing mode for faster iteration.
    if config['dev_mode']['enabled']:
        limit = config['dev_mode']['sample_size']
        query += f" ORDER BY a.\"fileCreatedAt\" DESC LIMIT {limit}"
        print(f"  - [DB] DEV MODE: Limiting fetch to {limit} most recent assets.")

    try:
        df = pd.read_sql_query(query, conn, params=params)
        if df.empty:
            print("  - [DB] No new assets found to process.")
        else:
            print(f"  - [DB] Successfully fetched {len(df)} assets.")
        return df
    except Exception as e:
        print(f"FATAL: Failed to execute asset query. Error: {e}", file=sys.stderr)
        sys.exit(1)