# app/services/database_service.py
"""
Provides a service for all interactions with the local SQLite database.

This class centralizes all SQL queries, making the application easier to
maintain and test. It handles the lifecycle of suggestions and scan logs.
"""
import sqlite3
import json
import logging
from datetime import datetime
from contextlib import contextmanager
from typing import Any, Literal, Optional, List, Dict, Iterator
from .config_service import config
from ..exceptions import DatabaseError

logger = logging.getLogger(__name__)

# Define a literal type for status strings for robust type checking.
SuggestionStatus = Literal['pending', 'approved', 'rejected', 'enriching', 'enrichment_failed', 'pending_enrichment', 'from_immich']

class DatabaseService:
    def __init__(self) -> None:
        db_path = config.project_root / "data" / "suggestions.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def get_connection(self) -> Iterator[sqlite3.Connection]:
        """Provides a managed database connection."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row # Allows accessing columns by name
            yield conn
        except sqlite3.Error as e:
            logger.error(f"SQLite database connection failed: {e}", exc_info=True)
            raise DatabaseError("Could not connect to the suggestions database.") from e
        finally:
            if 'conn' in locals() and conn:
                conn.close()

    def _init_db(self) -> None:
        """Initializes the database schema and performs any necessary migrations."""
        logger.info(f"Initializing suggestions database at {self.db_path}")
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # Create main suggestions table
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL DEFAULT 'pending_enrichment',
                    created_at TIMESTAMP NOT NULL,
                    event_start_date TIMESTAMP,
                    location TEXT,
                    vlm_title TEXT,
                    vlm_description TEXT,
                    strong_asset_ids_json TEXT,
                    weak_asset_ids_json TEXT,
                    cover_asset_id TEXT
                )""")
                
                # Create logs table
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS scan_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                )""")

                # Simple, idempotent migration: Add columns if they don't exist.
                # In a larger project, a more formal migration tool (like Alembic) would be used.
                self._add_column_if_not_exists(cursor, 'suggestions', 'event_start_date', 'TIMESTAMP')
                self._add_column_if_not_exists(cursor, 'suggestions', 'event_end_date', 'TIMESTAMP')
                self._add_column_if_not_exists(cursor, 'suggestions', 'location', 'TEXT')
                self._add_column_if_not_exists(cursor, 'suggestions', 'immich_album_id', 'TEXT')
                self._add_column_if_not_exists(cursor, 'suggestions', 'additional_asset_ids_json', 'TEXT')

                conn.commit()
                logger.debug("Database schema initialized/verified.")
        except Exception as e:
            logger.critical("Failed to initialize database schema.", exc_info=True)
            raise DatabaseError("Failed to initialize database schema.") from e

    def _add_column_if_not_exists(self, cursor: sqlite3.Cursor, table: str, column: str, col_type: str) -> None:
        """A utility to safely add a column to a table."""
        # Whitelist valid table and column names to prevent SQL injection
        valid_tables = ['suggestions', 'scan_logs']
        valid_columns = ['event_start_date', 'event_end_date', 'location', 'immich_album_id', 'additional_asset_ids_json']
        valid_types = ['TIMESTAMP', 'TEXT', 'INTEGER', 'REAL', 'BLOB']
        
        if table not in valid_tables:
            raise ValueError(f"Invalid table name: {table}")
        if column not in valid_columns:
            raise ValueError(f"Invalid column name: {column}")
        if col_type not in valid_types:
            raise ValueError(f"Invalid column type: {col_type}")
        
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row['name'] for row in cursor.fetchall()]
        if column not in columns:
            logger.info(f"Schema migration: Adding column '{column}' to table '{table}'.")
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    def get_pending_suggestions(self, sort_by: str = 'image_count', sort_order: str = 'desc') -> List[Dict[str, Any]]:
        """Fetches all suggestions that require user action or processing."""
        # Validate sort parameters to prevent SQL injection
        valid_sort_fields = ['created_at', 'event_start_date', 'image_count']
        valid_sort_orders = ['asc', 'desc']
        
        if sort_by not in valid_sort_fields:
            sort_by = 'image_count'
        if sort_order not in valid_sort_orders:
            sort_order = 'desc'
            
        # Build the ORDER BY clause based on sort_by
        if sort_by == 'image_count':
            order_clause = f"(LENGTH(strong_asset_ids_json) - LENGTH(REPLACE(strong_asset_ids_json, ',', '')) + CASE WHEN strong_asset_ids_json = '[]' THEN 0 ELSE 1 END) {sort_order.upper()}"
        elif sort_by == 'event_start_date':
            order_clause = f"event_start_date {sort_order.upper()}"
        else:  # created_at
            order_clause = f"created_at {sort_order.upper()}"
            
        query = f"""
            SELECT * FROM suggestions 
            WHERE status IN ('pending', 'pending_enrichment', 'enriching', 'enrichment_failed', 'from_immich')
            ORDER BY {order_clause}
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to fetch pending suggestions.", exc_info=True)
            raise DatabaseError("Could not retrieve pending suggestions.") from e

    def get_suggestion_details(self, suggestion_id: int) -> Optional[Dict[str, Any]]:
        """Fetches all data for a single suggestion by its ID."""
        if not isinstance(suggestion_id, int): return None
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to fetch details for suggestion {suggestion_id}.", exc_info=True)
            raise DatabaseError(f"Could not retrieve suggestion {suggestion_id}.") from e

    def store_initial_suggestion(self, candidate: Dict[str, Any], location: Optional[str]) -> int:
        """
        Stores a new album candidate found by the clustering pass.

        Args:
            candidate: A dictionary containing the clustered asset data.
            location: The primary location name determined by the geocoder.

        Returns:
            The integer ID of the newly created suggestion record.
        
        Raises:
            DatabaseError: If the suggestion could not be stored.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                all_ids = candidate.get('strong_asset_ids', []) + candidate.get('weak_asset_ids', [])
                cursor.execute("""
                INSERT INTO suggestions (status, created_at, event_start_date, event_end_date, location, vlm_title, vlm_description, strong_asset_ids_json, weak_asset_ids_json, cover_asset_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    'pending_enrichment',
                    datetime.now(),
                    candidate['min_date'].to_pydatetime(),
                    candidate['max_date'].to_pydatetime() if 'max_date' in candidate else candidate['min_date'].to_pydatetime(),
                    location,
                    config.get('defaults.title_template').format(date_str=candidate['min_date'].strftime('%B %Y')),
                    config.get('defaults.description'),
                    json.dumps(candidate.get('strong_asset_ids', [])),
                    json.dumps(candidate.get('weak_asset_ids', [])),
                    all_ids[0] if all_ids else None
                ))
                conn.commit()
                new_id = cursor.lastrowid
                logger.info(f"Stored new suggestion candidate with ID: {new_id}")
                return new_id
        except Exception as e:
            logger.error("Failed to store initial suggestion.", exc_info=True)
            raise DatabaseError("Could not store new suggestion.") from e

    def update_suggestion_with_analysis(self, suggestion_id: int, analysis: Dict[str, Any]) -> None:
        """
        Updates a suggestion with VLM results and sets status to 'pending' for review.

        Args:
            suggestion_id: The ID of the suggestion to update.
            analysis: A dictionary containing 'vlm_title', 'vlm_description', and 'cover_asset_id'.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                UPDATE suggestions
                SET status = 'pending', vlm_title = ?, vlm_description = ?, cover_asset_id = ?
                WHERE id = ?
                """, (
                    analysis.get('vlm_title'),
                    analysis.get('vlm_description'),
                    analysis.get('cover_asset_id'),
                    suggestion_id
                ))
                conn.commit()
            logger.info(f"Successfully enriched suggestion {suggestion_id} with VLM analysis.")
        except Exception as e:
            logger.error(f"Failed to update suggestion {suggestion_id} with analysis.", exc_info=True)
            raise DatabaseError("Could not update suggestion with VLM results.") from e

    def update_suggestion_status(self, suggestion_id: int, status: SuggestionStatus) -> None:
        """
        Updates only the status of a suggestion (e.g., 'approved', 'rejected').

        Args:
            suggestion_id: The ID of the suggestion to update.
            status: The new status string. Must be one of the `SuggestionStatus` types.
        """
        VALID_STATUSES = ['pending', 'approved', 'rejected', 'enriching', 'enrichment_failed', 'pending_enrichment']
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE suggestions SET status = ? WHERE id = ?", (status, suggestion_id))
                conn.commit()
            logger.info(f"Updated status for suggestion {suggestion_id} to '{status}'.")
        except Exception as e:
            logger.error(f"Failed to update status for suggestion {suggestion_id}.", exc_info=True)
            raise DatabaseError("Could not update suggestion status.") from e

    def update_suggestion_title(self, suggestion_id: int, title: str) -> None:
        """
        Updates the title of a suggestion.

        Args:
            suggestion_id: The ID of the suggestion to update.
            title: The new title string.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE suggestions SET vlm_title = ? WHERE id = ?", (title, suggestion_id))
                conn.commit()
            logger.info(f"Updated title for suggestion {suggestion_id} to '{title}'.")
        except Exception as e:
            logger.error(f"Failed to update title for suggestion {suggestion_id}.", exc_info=True)
            raise DatabaseError("Could not update suggestion title.") from e

    def update_suggestion_cover(self, suggestion_id: int, cover_asset_id: str) -> None:
        """
        Updates the cover asset ID of a suggestion.

        Args:
            suggestion_id: The ID of the suggestion to update.
            cover_asset_id: The new cover asset ID.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE suggestions SET cover_asset_id = ? WHERE id = ?", (cover_asset_id, suggestion_id))
                conn.commit()
            logger.info(f"Updated cover for suggestion {suggestion_id} to asset '{cover_asset_id}'.")
        except Exception as e:
            logger.error(f"Failed to update cover for suggestion {suggestion_id}.", exc_info=True)
            raise DatabaseError("Could not update suggestion cover.") from e

    def delete_all_pending_suggestions(self) -> int:
        """
        Deletes all pending suggestions from the database.
        
        Returns:
            The number of suggestions that were deleted.
        
        Raises:
            DatabaseError: If the deletion fails.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # Delete suggestions that are in pending states
                cursor.execute("""
                    DELETE FROM suggestions 
                    WHERE status IN ('pending', 'pending_enrichment', 'enriching', 'enrichment_failed')
                """)
                deleted_count = cursor.rowcount
                conn.commit()
            logger.info(f"Deleted {deleted_count} pending suggestions.")
            return deleted_count
        except Exception as e:
            logger.error("Failed to delete pending suggestions.", exc_info=True)
            raise DatabaseError("Could not delete pending suggestions.") from e

    def merge_suggestions(self, suggestion_ids: List[int], merged_title: Optional[str] = None) -> int:
        """
        Merges multiple suggestions into a single suggestion.
        
        Args:
            suggestion_ids: List of suggestion IDs to merge (minimum 2)
            merged_title: Optional title for the merged suggestion
            
        Returns:
            The ID of the merged suggestion (the first one in the list)
            
        Raises:
            DatabaseError: If the merge fails
            ValueError: If less than 2 suggestions provided
        """
        logger.info(f"merge_suggestions called with IDs: {suggestion_ids}")
        
        if len(suggestion_ids) < 2:
            raise ValueError("At least 2 suggestions are required for merging")
            
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Get all suggestions to merge
                placeholders = ','.join(['?' for _ in suggestion_ids])
                cursor.execute(f"""
                    SELECT id, strong_asset_ids_json, weak_asset_ids_json, event_start_date, event_end_date, location, vlm_title
                    FROM suggestions 
                    WHERE id IN ({placeholders}) AND status IN ('pending', 'pending_enrichment', 'enriching', 'enrichment_failed')
                    ORDER BY event_start_date ASC
                """, suggestion_ids)
                
                suggestions = cursor.fetchall()
                logger.info(f"Found {len(suggestions)} suggestions to merge")
                
                if len(suggestions) != len(suggestion_ids):
                    logger.error(f"Mismatch: requested {len(suggestion_ids)} suggestions, found {len(suggestions)}")
                    raise ValueError("Some suggestions not found or not in mergeable state")
                
                # Use the first suggestion as the base
                base_suggestion = suggestions[0]
                base_id = base_suggestion['id']
                logger.info(f"Using suggestion {base_id} as base for merge")
                
                # Combine all asset IDs
                all_strong_ids = set()
                all_weak_ids = set()
                earliest_date = None
                latest_date = None
                locations = set()
                
                for suggestion in suggestions:
                    # Merge asset IDs
                    strong_ids = json.loads(suggestion['strong_asset_ids_json'] or '[]')
                    weak_ids = json.loads(suggestion['weak_asset_ids_json'] or '[]')
                    all_strong_ids.update(strong_ids)
                    all_weak_ids.update(weak_ids)
                    
                    # Track date range
                    start_date = suggestion['event_start_date']
                    end_date = suggestion['event_end_date']
                    
                    if start_date:
                        if isinstance(start_date, str):
                            from datetime import datetime
                            try:
                                start_date = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                            except ValueError:
                                start_date = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S.%f')
                        
                        if earliest_date is None or start_date < earliest_date:
                            earliest_date = start_date
                    
                    if end_date:
                        if isinstance(end_date, str):
                            from datetime import datetime
                            try:
                                end_date = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                            except ValueError:
                                end_date = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S.%f')
                        
                        if latest_date is None or end_date > latest_date:
                            latest_date = end_date
                    
                    # Collect locations
                    if suggestion['location']:
                        locations.add(suggestion['location'])
                
                # Choose primary location (most common, or first if tie)
                primary_location = max(locations, key=lambda x: sum(1 for s in suggestions if s['location'] == x)) if locations else None
                
                # Use provided title or combine existing titles
                if not merged_title:
                    titles = [s['vlm_title'] for s in suggestions if s['vlm_title']]
                    if titles:
                        merged_title = f"Merged Album ({', '.join(set(titles))})"
                    else:
                        merged_title = f"Merged Album ({len(suggestions)} albums)"
                
                # Update the base suggestion with merged data
                cursor.execute("""
                    UPDATE suggestions 
                    SET strong_asset_ids_json = ?, 
                        weak_asset_ids_json = ?, 
                        event_start_date = ?,
                        event_end_date = ?,
                        location = ?,
                        vlm_title = ?,
                        status = 'pending_enrichment'
                    WHERE id = ?
                """, (
                    json.dumps(list(all_strong_ids)),
                    json.dumps(list(all_weak_ids)),
                    earliest_date,
                    latest_date or earliest_date,
                    primary_location,
                    merged_title,
                    base_id
                ))
                
                # Delete the other suggestions
                other_ids = [s['id'] for s in suggestions if s['id'] != base_id]
                if other_ids:
                    other_placeholders = ','.join(['?' for _ in other_ids])
                    cursor.execute(f"DELETE FROM suggestions WHERE id IN ({other_placeholders})", other_ids)
                
                conn.commit()
                
                logger.info(f"Successfully merged {len(suggestion_ids)} suggestions into suggestion {base_id}")
                logger.info(f"Deleted {len(other_ids) if other_ids else 0} other suggestions")
                logger.info(f"Updated base suggestion {base_id} with {len(all_strong_ids)} strong assets and {len(all_weak_ids)} weak assets")
                return base_id
                
        except Exception as e:
            logger.error(f"Failed to merge suggestions {suggestion_ids}.", exc_info=True)
            raise DatabaseError("Could not merge suggestions.") from e
            
    def get_processed_asset_ids(self) -> List[str]:
        """Gets all asset IDs that are already part of any existing suggestion."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT strong_asset_ids_json, weak_asset_ids_json FROM suggestions")
                rows = cursor.fetchall()
                
                processed_ids = set()
                for strong_json, weak_json in rows:
                    processed_ids.update(json.loads(strong_json or '[]'))
                    processed_ids.update(json.loads(weak_json or '[]'))
                return list(processed_ids)
        except Exception as e:
            logger.error("Failed to get processed asset IDs.", exc_info=True)
            raise DatabaseError("Could not retrieve processed asset IDs.") from e

    def log_to_db(self, level: str, message: str) -> None:
        """Writes a log entry to the SQLite database for the UI to display."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO scan_logs (timestamp, level, message) VALUES (?, ?, ?)", 
                               (datetime.now(), level.upper(), message))
                conn.commit()
        except Exception as e:
            # If we can't log to the DB, log the log message and the error to the file log.
            logger.error(f"Failed to write log to database. Original message: '{message}'", exc_info=True)

    def get_scan_logs(self, last_id: int = 0) -> List[Dict[str, Any]]:
        """Fetches all scan log entries since a given ID."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, level, message FROM scan_logs WHERE id > ? ORDER BY id ASC", (last_id,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to fetch scan logs from database.", exc_info=True)
            return [] # Return empty on failure to avoid breaking the UI
    
    def store_immich_album_as_suggestion(self, album_data: Dict[str, Any]) -> int:
        """
        Stores an existing Immich album as a suggestion with 'from_immich' status.
        Prevents duplicates by checking if the album already exists.
        
        Args:
            album_data: Dictionary containing album metadata from Immich API
            
        Returns:
            The integer ID of the suggestion record (existing or newly created).
            
        Raises:
            DatabaseError: If the suggestion could not be stored.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                album_id = album_data.get('album_id')
                
                # Check if this Immich album already exists in our database
                cursor.execute("""
                    SELECT id FROM suggestions 
                    WHERE immich_album_id = ? AND status = 'from_immich'
                """, (album_id,))
                existing = cursor.fetchone()
                
                if existing:
                    suggestion_id = existing[0]
                    logger.debug(f"Immich album '{album_data.get('title')}' already exists as suggestion #{suggestion_id} - skipping duplicate")
                    return suggestion_id
                
                # Album doesn't exist, create new record
                cursor.execute("""
                INSERT INTO suggestions (status, created_at, event_start_date, event_end_date, location, vlm_title, vlm_description, strong_asset_ids_json, weak_asset_ids_json, cover_asset_id, immich_album_id, additional_asset_ids_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    'from_immich',
                    datetime.now(),
                    album_data.get('start_date'),
                    album_data.get('end_date'),
                    album_data.get('location'),
                    album_data.get('title'),
                    album_data.get('description'),
                    json.dumps(album_data.get('asset_ids', [])),
                    json.dumps([]),  # weak_asset_ids_json - empty for existing albums
                    album_data.get('cover_asset_id'),
                    album_data.get('album_id'),
                    json.dumps(album_data.get('additional_asset_ids', []))  # Potential additions
                ))
                suggestion_id = cursor.lastrowid
                conn.commit()  # Force commit immediately
                logger.info(f"Stored new Immich album '{album_data.get('title')}' as suggestion #{suggestion_id}")
                return suggestion_id
        except Exception as e:
            logger.error(f"Failed to store Immich album as suggestion: {e}", exc_info=True)
            raise DatabaseError(f"Could not store Immich album as suggestion: {e}") from e
    
    def cleanup_deleted_immich_albums(self, current_immich_album_ids: List[str]) -> int:
        """
        Removes suggestion records for Immich albums that no longer exist.
        
        Args:
            current_immich_album_ids: List of album IDs currently in Immich
            
        Returns:
            Number of deleted suggestion records
            
        Raises:
            DatabaseError: If the cleanup operation fails
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Find existing from_immich suggestions
                cursor.execute("""
                    SELECT id, immich_album_id, vlm_title FROM suggestions 
                    WHERE status = 'from_immich' AND immich_album_id IS NOT NULL
                """)
                existing_suggestions = cursor.fetchall()
                
                deleted_count = 0
                for suggestion in existing_suggestions:
                    suggestion_id, album_id, title = suggestion
                    
                    # If this album no longer exists in Immich, delete the suggestion
                    if album_id not in current_immich_album_ids:
                        cursor.execute("DELETE FROM suggestions WHERE id = ?", (suggestion_id,))
                        logger.info(f"Deleted suggestion #{suggestion_id} for removed Immich album '{title}' (ID: {album_id})")
                        deleted_count += 1
                
                conn.commit()
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} suggestions for deleted Immich albums")
                return deleted_count
                
        except Exception as e:
            logger.error(f"Failed to cleanup deleted Immich albums: {e}", exc_info=True)
            raise DatabaseError(f"Could not cleanup deleted Immich albums: {e}") from e
    
    def remove_duplicate_immich_albums(self) -> int:
        """
        Removes duplicate Immich album suggestions, keeping only the most recent one for each album_id.
        
        Returns:
            Number of duplicate suggestions removed
            
        Raises:
            DatabaseError: If the cleanup operation fails
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Find duplicates: albums with same immich_album_id
                cursor.execute("""
                    SELECT immich_album_id, COUNT(*) as count, GROUP_CONCAT(id) as ids
                    FROM suggestions 
                    WHERE status = 'from_immich' AND immich_album_id IS NOT NULL
                    GROUP BY immich_album_id
                    HAVING COUNT(*) > 1
                """)
                duplicates = cursor.fetchall()
                
                deleted_count = 0
                for row in duplicates:
                    album_id, count, ids_str = row
                    suggestion_ids = [int(id_str) for id_str in ids_str.split(',')]
                    
                    # Keep the most recent one (highest ID), delete the others
                    suggestion_ids.sort()
                    ids_to_delete = suggestion_ids[:-1]  # All except the last (most recent)
                    
                    for suggestion_id in ids_to_delete:
                        cursor.execute("DELETE FROM suggestions WHERE id = ?", (suggestion_id,))
                        deleted_count += 1
                    
                    logger.info(f"Removed {len(ids_to_delete)} duplicate suggestions for Immich album {album_id}")
                
                conn.commit()
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} duplicate Immich album suggestions")
                return deleted_count
                
        except Exception as e:
            logger.error(f"Failed to remove duplicate Immich albums: {e}", exc_info=True)
            raise DatabaseError(f"Could not remove duplicate Immich albums: {e}") from e

# Singleton instance for easy access throughout the application.
db_service = DatabaseService()