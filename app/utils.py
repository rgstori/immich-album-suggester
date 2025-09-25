# app/utils.py
"""
Core utility functions for the Immich Album Suggester application.

This module provides common utilities used across multiple components,
including datetime parsing, error handling, and other shared functionality.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, Union, Any, Callable, TypeVar
import logging
import functools
import traceback

logger = logging.getLogger(__name__)

T = TypeVar('T')

def parse_datetime_safe(date_value: Union[str, datetime, None]) -> Optional[datetime]:
    """
    Centralized datetime parsing with comprehensive format support and error handling.
    
    This function handles multiple datetime formats commonly found in the application:
    - ISO formats with/without timezone info
    - Database datetime formats
    - EXIF datetime formats
    - Custom application formats
    
    Args:
        date_value: Date value to parse (string, datetime object, or None)
        
    Returns:
        Parsed datetime object or None if parsing fails
        
    Examples:
        >>> parse_datetime_safe("2025-08-15T10:30:00Z")
        datetime(2025, 8, 15, 10, 30, 0, tzinfo=timezone.utc)
        
        >>> parse_datetime_safe("2025-08-15 10:30:00.123456")
        datetime(2025, 8, 15, 10, 30, 0, 123456)
        
        >>> parse_datetime_safe("invalid")
        None
    """
    if not date_value:
        return None
    
    if isinstance(date_value, datetime):
        return date_value
    
    if not isinstance(date_value, str):
        logger.warning(f"Unexpected date type: {type(date_value)}, value: {date_value}")
        return None
    
    # Clean up common formatting issues
    date_str = date_value.strip()
    if not date_str:
        return None
    
    # List of datetime formats to try, ordered by likelihood
    formats = [
        # ISO formats (most common)
        '%Y-%m-%dT%H:%M:%S.%fZ',      # ISO with microseconds and Z
        '%Y-%m-%dT%H:%M:%SZ',         # ISO with Z
        '%Y-%m-%dT%H:%M:%S.%f',       # ISO with microseconds
        '%Y-%m-%dT%H:%M:%S',          # ISO basic
        
        # Database formats
        '%Y-%m-%d %H:%M:%S.%f',       # SQLite/PostgreSQL with microseconds
        '%Y-%m-%d %H:%M:%S',          # SQLite/PostgreSQL basic
        
        # EXIF and camera formats
        '%Y:%m:%d %H:%M:%S',          # Common EXIF format
        '%Y/%m/%d %H:%M:%S',          # Alternative camera format
        
        # Date-only formats
        '%Y-%m-%d',                   # ISO date only
        '%Y/%m/%d',                   # Alternative date format
        '%Y:%m:%d',                   # EXIF date only
        
        # Alternative formats
        '%d-%m-%Y %H:%M:%S',          # European format
        '%m/%d/%Y %H:%M:%S',          # US format
    ]
    
    # First, try ISO format with timezone handling (most common case)
    try:
        # Handle timezone suffixes
        if date_str.endswith('Z'):
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        elif '+' in date_str[-6:] or date_str.endswith(('UTC', 'GMT')):
            # Try direct ISO parsing for timezone-aware strings
            return datetime.fromisoformat(date_str.replace('UTC', '+00:00').replace('GMT', '+00:00'))
        else:
            # Try direct ISO parsing first
            return datetime.fromisoformat(date_str)
    except ValueError:
        pass
    
    # Try each format in order
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    
    # Log parsing failure for debugging
    logger.debug(f"Failed to parse datetime: '{date_value}' (type: {type(date_value)})")
    return None


def service_error_handler(
    operation_name: str,
    default_return: Any = None,
    raise_on_error: bool = True,
    log_level: str = "ERROR"
):
    """
    Decorator for standardized service layer error handling.
    
    This decorator provides consistent error handling across all service methods:
    - Logs errors with context
    - Converts exceptions to application-specific exceptions
    - Provides fallback return values
    - Maintains consistent error messages
    
    Args:
        operation_name: Human-readable description of the operation
        default_return: Value to return if error occurs and raise_on_error=False
        raise_on_error: Whether to raise exceptions or return default
        log_level: Logging level for error messages
        
    Returns:
        Decorator function
        
    Example:
        @service_error_handler("fetch user data", default_return=[])
        def get_users(self):
            # Service implementation
            pass
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_msg = f"Failed to {operation_name}: {str(e)}"
                
                # Log with appropriate level
                log_func = getattr(logger, log_level.lower(), logger.error)
                log_func(f"{error_msg}\n{traceback.format_exc()}")
                
                if raise_on_error:
                    # Import here to avoid circular imports
                    from .exceptions import AppServiceError
                    raise AppServiceError(error_msg) from e
                else:
                    return default_return
                    
        return wrapper
    return decorator


def validate_asset_id(asset_id: str) -> bool:
    """
    Validate that an asset ID is in the expected format.
    
    Args:
        asset_id: Asset ID to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not asset_id or not isinstance(asset_id, str):
        return False
    
    # Basic validation - asset IDs should be non-empty strings
    # Could be enhanced with specific format validation if needed
    return len(asset_id.strip()) > 0


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """
    Sanitize a filename for safe filesystem operations.
    
    Args:
        filename: Original filename
        max_length: Maximum allowed length
        
    Returns:
        Sanitized filename safe for filesystem use
    """
    if not filename:
        return "untitled"
    
    # Remove/replace problematic characters
    import re
    
    # Replace problematic characters with underscores
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
    
    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip(' .')
    
    # Ensure not empty
    if not sanitized:
        sanitized = "untitled"
    
    # Truncate if too long
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip(' .')
    
    return sanitized


def safe_dict_get(data: dict, key_path: str, default: Any = None) -> Any:
    """
    Safely get nested dictionary values using dot notation.
    
    Args:
        data: Dictionary to search
        key_path: Dot-separated path (e.g., "user.profile.name")
        default: Default value if key not found
        
    Returns:
        Value at key path or default
        
    Example:
        >>> data = {"user": {"profile": {"name": "John"}}}
        >>> safe_dict_get(data, "user.profile.name")
        "John"
        >>> safe_dict_get(data, "user.profile.age", 0)
        0
    """
    try:
        keys = key_path.split('.')
        result = data
        for key in keys:
            if isinstance(result, dict) and key in result:
                result = result[key]
            else:
                return default
        return result
    except (AttributeError, TypeError):
        return default


def chunk_list(items: list, chunk_size: int) -> list[list]:
    """
    Split a list into chunks of specified size.
    
    Args:
        items: List to chunk
        chunk_size: Size of each chunk
        
    Returns:
        List of chunks
        
    Example:
        >>> chunk_list([1, 2, 3, 4, 5], 2)
        [[1, 2], [3, 4], [5]]
    """
    if chunk_size <= 0:
        raise ValueError("Chunk size must be positive")
    
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Formatted size string (e.g., "1.5 MB")
    """
    if size_bytes < 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    size = float(size_bytes)
    
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def retry_on_failure(
    max_attempts: int = 3,
    delay_seconds: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Decorator to retry function calls on failure with exponential backoff.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay_seconds: Initial delay between retries
        backoff_factor: Multiplier for delay on each retry
        exceptions: Tuple of exceptions to catch and retry on
        
    Returns:
        Decorator function
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts - 1:
                        # Last attempt failed, re-raise
                        raise
                    
                    # Calculate delay for next attempt
                    delay = delay_seconds * (backoff_factor ** attempt)
                    logger.warning(
                        f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: {e}. "
                        f"Retrying in {delay:.1f} seconds..."
                    )
                    
                    import time
                    time.sleep(delay)
            
            # Should never reach here, but just in case
            raise last_exception
            
        return wrapper
    return decorator