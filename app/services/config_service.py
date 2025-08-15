# app/services/config_service.py
"""
Provides a singleton configuration service for the entire application.

This service is responsible for:
1. Loading the `config.yaml` file.
2. Loading environment variables from a `.env` file.
3. Setting up a centralized logging system for both console and file output.

Using a singleton pattern ensures that configuration is loaded once and is
consistent across all modules that import it.
"""
import yaml
import os
import logging
import sys
from pathlib import Path
import dotenv
import threading
from typing import Any, Dict, Optional, Union

class AppConfig:
    _instance: Optional['AppConfig'] = None
    _loaded: bool = False
    _lock: threading.Lock = threading.Lock()

    def __new__(cls, *args, **kwargs) -> 'AppConfig':
        if cls._instance is None:
            with cls._lock:
                # Double-check pattern to prevent race conditions
                if cls._instance is None:
                    cls._instance = super(AppConfig, cls).__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # The __init__ might be called multiple times, but the loading logic
        # is protected by the `_loaded` flag and thread lock.
        if not self._loaded:
            with self._lock:
                # Double-check pattern inside lock
                if not self._loaded:
                    # Load environment variables first, as they might be needed for config.
                    dotenv.load_dotenv()
                    self.project_root = Path(__file__).resolve().parents[2]
                    
                    self._load_yaml_config()
                    self._load_env_vars()
                    self._setup_logging()
                    
                    self._loaded = True
                    logging.info("Application configuration and logging initialized successfully.")

    def _load_yaml_config(self) -> None:
        """Loads the main config.yaml file."""
        config_path = self.project_root / 'config.yaml'
        try:
            with open(config_path, 'r') as f:
                self.yaml = yaml.safe_load(f)
        except FileNotFoundError:
            # A missing config file is a fatal error.
            print(f"FATAL: Configuration file not found at {config_path}", file=sys.stderr)
            sys.exit(1)
        except yaml.YAMLError as e:
            print(f"FATAL: Error parsing YAML configuration file: {e}", file=sys.stderr)
            sys.exit(1)

    def _load_env_vars(self) -> None:
        """Loads all required and optional environment variables."""
        self.immich_url = os.getenv("IMMICH_URL")
        self.immich_api_key = os.getenv("IMMICH_API_KEY")
        self.postgres_db = os.getenv("POSTGRES_DB")
        self.postgres_user = os.getenv("POSTGRES_USER")
        self.postgres_password = os.getenv("POSTGRES_PASSWORD")
        self.db_hostname = os.getenv("DB_HOSTNAME")
        self.db_port = os.getenv("DB_PORT")

    def _setup_logging(self) -> None:
        """Configures the root logger for consistent logging across the app."""
        # Check if logging has already been configured to prevent double setup
        root_logger = logging.getLogger()
        if root_logger.handlers:
            # Logging already configured, just set our level and return
            log_config = self.get('logging', {})
            log_level_str = log_config.get('level', 'INFO').upper()
            log_level = getattr(logging, log_level_str, logging.INFO)
            root_logger.setLevel(log_level)
            return
            
        log_config = self.get('logging', {})
        log_level_str = log_config.get('level', 'INFO').upper()
        log_level = getattr(logging, log_level_str, logging.INFO)
        
        log_dir = self.project_root / log_config.get('directory', 'logs')
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / log_config.get('filename', 'app.log')

        # Configure the root logger. This is the most effective way to ensure
        # all child loggers inherit the same settings.
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - [%(levelname)s] - %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout) # Log to console (stdout)
            ],
            force=True  # Force reconfiguration if needed
        )

        # Silence overly verbose libraries if needed
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Safely retrieves a value from the nested YAML configuration.
        
        Args:
            key_path (str): A dot-separated path to the desired key (e.g., 'vlm.model').
            default: The value to return if the key is not found.
            
        Returns:
            The configuration value or the default.
        """
        value = self.yaml
        try:
            for key in key_path.split('.'):
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default

# Create the singleton instance that will be imported by other modules.
config = AppConfig()