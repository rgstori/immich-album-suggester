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
import logging
from psycopg2.extras import RealDictCursor

# Configure logging to avoid exposing sensitive data
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _get_schema_name(config: dict | None) -> str:
    """
    Determine the Postgres schema to use. Preference order:
    1) config['postgres']['schema'] (if provided)
    2) DB_SCHEMA environment variable
    3) 'public' (default)
    """
    if config and isinstance(config, dict):
        schema_cfg = config.get('postgres', {})
        if isinstance(schema_cfg, dict) and schema_cfg.get('schema'):
            return schema_cfg['schema']
    return os.getenv("DB_SCHEMA", "public")


def get_connection():
    """
    Establishes and returns a connection to the Immich PostgreSQL database
    using credentials from environment variables.
    """
    try:
        # Log connection attempt without sensitive data
        logger.info("Attempting database connection")

        conn = psycopg2.connect(
            dbname=os.getenv("POSTGRES_DB"),
            user=os.getenv("POSTGRES_USER"),
            password=os.getenv("POSTGRES_PASSWORD"),
            host=os.getenv("DB_HOSTNAME"),
            port=os.getenv("DB_PORT"),
            cursor_factory=RealDictCursor
        )
        return conn
    except psycopg2.OperationalError as e:
        print(f"FATAL: Could not connect to the Immich database. Error: {e}", file=sys.stderr)
        sys.exit(1)


def _schema_exists(conn, schema: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.schemata
                WHERE schema_name = %s
            )
        """, (schema,))
        # RealDictCursor returns dict; default returns tuple
        row = cur.fetchone()
        return (row[0] if not isinstance(row, dict) else list(row.values())[0])


def _table_exists(conn, schema: str, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s
            )
        """, (schema, table))
        row = cur.fetchone()
        return (row[0] if not isinstance(row, dict) else list(row.values())[0])


def _column_exists(conn, schema: str, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = %s
                  AND column_name = %s
            )
        """, (schema, table, column))
        row = cur.fetchone()
        # Support both tuple and dict rows
        return (row[0] if not isinstance(row, dict) else list(row.values())[0])


def _list_tables(conn, schema: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
            ORDER BY table_name
        """, (schema,))
        rows = cur.fetchall()
        # Support both tuple and dict rows
        names = []
        for r in rows:
            if isinstance(r, dict):
                names.append(r.get('table_name'))
            else:
                names.append(r[0])
        return names


def _resolve_table(conn, schema: str, candidates: list[str]) -> str | None:
    for name in candidates:
        if _table_exists(conn, schema, name):
            return name
    return None


def fetch_assets(conn, config: dict, excluded_asset_ids: list) -> pd.DataFrame:
    """
    Fetches all non-deleted assets from the Immich PostgreSQL database, joining with
    EXIF and smart_search tables to get all necessary data for clustering.

    Args:
        conn: An active psycopg2 database connection.
        config: The application configuration dictionary.
        excluded_asset_ids: A list of asset IDs to exclude from the query.

    Returns:
        A pandas DataFrame containing all necessary asset information.
    """
    print("  - [DB] Fetching asset data from Immich database...")

    # Determine schema and verify existence
    schema = _get_schema_name(config)
    if not _schema_exists(conn, schema):
        print(f"FATAL: PostgreSQL schema '{schema}' does not exist.", file=sys.stderr)
        with conn.cursor() as cur:
            cur.execute("SELECT schema_name FROM information_schema.schemata ORDER BY schema_name")
            rows = cur.fetchall()
            available = []
            for r in rows:
                if isinstance(r, dict):
                    available.append(r.get('schema_name'))
                else:
                    available.append(r[0])
        print(f"       Available schemas: {', '.join(available)}", file=sys.stderr)
        print("       Set DB_SCHEMA env var or config.postgres.schema to the correct schema.", file=sys.stderr)
        sys.exit(1)

    # Resolve table names across Immich versions
    asset_tbl = _resolve_table(conn, schema, ["asset", "assets"])
    exif_tbl = _resolve_table(conn, schema, ["asset_exif", "exif"])
    smart_tbl = _resolve_table(conn, schema, ["smart_search"])

    if not asset_tbl or not exif_tbl or not smart_tbl:
        tables = _list_tables(conn, schema)
        print("FATAL: Required Immich tables not found in the target schema.", file=sys.stderr)
        print(f"       Expected candidates:", file=sys.stderr)
        print(f"         - asset table: one of ['asset', 'assets'] -> resolved: {asset_tbl}", file=sys.stderr)
        print(f"         - exif table: one of ['asset_exif', 'exif'] -> resolved: {exif_tbl}", file=sys.stderr)
        print(f"         - smart_search table: ['smart_search'] -> resolved: {smart_tbl}", file=sys.stderr)
        print(f"       Tables present in schema '{schema}': {', '.join(tables)}", file=sys.stderr)
        print("       Tip: Verify your Immich version and schema, and adjust DB_SCHEMA/config.postgres.schema if needed.", file=sys.stderr)
        sys.exit(1)

    print(f"  - [DB] Using schema '{schema}' with tables: asset='{asset_tbl}', exif='{exif_tbl}', smart_search='{smart_tbl}'")

    # Build dynamic filters depending on available columns (e.g., isArchived may not exist)
    # We always filter out soft-deleted assets (deletedAt IS NULL).
    filters = ['a."deletedAt" IS NULL']
    has_is_archived_camel = _column_exists(conn, schema, asset_tbl, "isArchived")
    has_is_archived_snake = _column_exists(conn, schema, asset_tbl, "is_archived")
    if has_is_archived_camel:
        filters.append('COALESCE(a."isArchived", false) = false')
    elif has_is_archived_snake:
        filters.append('COALESCE(a."is_archived", false) = false')

    # The main query gathers all data in one pass.
    # LEFT JOIN is used for EXIF to include assets that may not have EXIF data.
    # INNER JOIN is used for smart_search, as assets without an embedding
    # cannot be processed by our clustering logic anyway.
    # IMPORTANT: Cast embedding to text for robust downstream parsing.
    query = f"""
    SELECT
        a.id as "assetId",
        a."fileCreatedAt",
        ae."dateTimeOriginal",
        ae.latitude,
        ae.longitude,
        s.embedding::text as embedding
    FROM
        "{schema}"."{asset_tbl}" a
    JOIN
        "{schema}"."{smart_tbl}" s ON a.id = s."assetId"
    LEFT JOIN
        "{schema}"."{exif_tbl}" ae ON a.id = ae."assetId"
    WHERE
        { ' AND '.join(filters) }
    """

    # Handle exclusions for incremental mode
    params = []
    if excluded_asset_ids:
        placeholders = ','.join(['%s'] * len(excluded_asset_ids))
        query += f" AND a.id NOT IN ({placeholders})"
        params.extend(excluded_asset_ids)

    # Always order results by newest first
    query += ' ORDER BY a."fileCreatedAt" DESC'

    # Apply limit for dev mode
    if config.get('dev_mode', {}).get('enabled'):
        limit = config.get('dev_mode', {}).get('sample_size', 0)
        if limit and isinstance(limit, int):
            query += f" LIMIT {limit}"
            print(f"  - [DB] DEV MODE: Limiting fetch to {limit} most recent assets.")
    print(f"  - [DB] Applying filters: {' AND '.join(filters)}")

    try:
        # Using cursor with context manager to ensure proper cleanup
        with conn.cursor() as cursor:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            # Fetch all results
            rows = cursor.fetchall()

        if not rows:
            logger.info("No new assets found to process.")
            return pd.DataFrame()

        # Convert to DataFrame
        df = pd.DataFrame(rows)
        logger.info(f"Successfully fetched {len(df)} assets.")
        return df

    except Exception as e:
        logger.error(f"Failed to execute asset query. Error: {e}")
        raise
    finally:
        if conn:
            conn.close()

def get_exif_for_asset(config: dict, asset_id: str) -> dict | None:
    """
    Fetches all available EXIF data for a single asset from the database.

    Args:
        config: The application configuration dictionary.
        asset_id: The ID of the asset to look up.

    Returns:
        A dictionary of EXIF data, or None if not found or an error occurs.
    """
    conn = None  # Initialize conn to None
    try:
        conn = get_connection()
        schema = _get_schema_name(config)
        
        # Resolve the correct table name for EXIF data
        exif_tbl = _resolve_table(conn, schema, ["asset_exif", "exif"])
        if not exif_tbl:
            logger.warning(f"Could not resolve EXIF table in schema '{schema}'")
            return None

        # Whitelist allowed schemas for security
        ALLOWED_SCHEMAS = {'public', 'immich'}
        if schema not in ALLOWED_SCHEMAS:
            logger.error(f"Schema '{schema}' not in allowed list")
            return None
        
        # Query for all columns for the given asset ID (safe after schema validation)
        query = f'SELECT * FROM "{schema}"."{exif_tbl}" WHERE "assetId" = %s'
        
        with conn.cursor() as cur:
            cur.execute(query, (asset_id,))
            row = cur.fetchone()

        if not row:
            return None

        # Convert the RealDictRow to a standard dict and remove the assetId
        exif_data = dict(row)
        exif_data.pop("assetId", None) # Don't show the ID in the UI display
        
        # Filter out keys that have None or empty values for a cleaner display
        return {k: v for k, v in exif_data.items() if v is not None and v != ''}

    except Exception as e:
        logger.error(f"Failed to fetch EXIF data for asset. Error: {e}")
        return None
    finally:
        if conn:
            conn.close()