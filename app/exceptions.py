# app/exceptions.py
"""
Defines custom, application-specific exceptions for clear error handling.

Using specific exceptions allows the rest of the application to catch and handle
different classes of errors in a more targeted way than relying on generic
Exception types. This is crucial for providing meaningful feedback to the user
and for robust logging.
"""

class AppServiceError(Exception):
    """Base exception for all service-related errors in the application."""
    pass

# --- VLM Service Exceptions ---
class VLMError(AppServiceError):
    """Base exception for all Vision Language Model related issues."""
    pass

class VLMConnectionError(VLMError):
    """Raised for network or connectivity issues when contacting the VLM service."""
    pass

class VLMResponseError(VLMError):
    """Raised when the VLM returns an invalid, empty, or malformed response."""
    pass

# --- Immich Service Exceptions ---
class ImmichServiceError(AppServiceError):
    """Base exception for errors related to the Immich service, either API or DB."""
    pass

class ImmichAPIError(ImmichServiceError):
    """Raised for specific failures when interacting with the Immich REST API."""
    pass

class ImmichDBError(ImmichServiceError):
    """Raised for specific failures when interacting with the Immich PostgreSQL DB."""
    pass

# --- Local Services Exceptions ---
class DatabaseError(AppServiceError):
    """Raised for errors related to the local suggestions.db SQLite database."""
    pass

class ProcessError(AppServiceError):
    """Raised for errors related to starting or managing background subprocesses."""
    pass