# app/services/__init__.py
"""
Initializes the services package and provides easy access to the singleton
service instances.

This pattern allows other parts of the application to import services with a
clean syntax, like so:
from app.services import db_service, immich_service
"""
# The order of these imports can matter if there are dependencies between them.
# config_service should generally be first.
from .config_service import config
from .database_service import db_service
from .immich_service import immich_service
from .process_service import process_service

# This line controls what is exported when a user does 'from app.services import *'
# It's good practice for package hygiene.
__all__ = [
    "config",
    "db_service",
    "immich_service",
    "process_service"
]