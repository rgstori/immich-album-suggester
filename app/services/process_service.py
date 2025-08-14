# app/services/process_service.py
"""
Provides a service for managing and monitoring background processes.

This class centralizes all logic related to `subprocess.Popen`, ensuring that
backend tasks (clustering, enrichment) are started, monitored, and cleaned up
in a consistent and robust manner.
"""
import subprocess
import sys
import os
import signal
import atexit
import logging
from typing import Literal
from .config_service import config
from ..exceptions import ProcessError

logger = logging.getLogger(__name__)

# Define a literal type for scan modes.
ScanMode = Literal['incremental', 'full']

class ProcessService:
    def __init__(self):
        # A dictionary to hold references to running Popen objects.
        # The key is a unique identifier (e.g., 'scan' or 'enrich_123').
        self.processes = {}
        
        # Register cleanup handlers for graceful shutdown
        self._register_cleanup_handlers()

    def _get_base_command(self) -> list[str]:
        """Constructs the base command for running the backend script."""
        # Using `sys.executable` ensures we use the same Python interpreter.
        # Using `-m app.main` is the correct way to run a module within a package,
        # ensuring all relative imports work as expected.
        return [sys.executable, "-m", "app.main"]

    def _start_process(self, process_key: str, command: list[str]) -> None:
        """A generic helper to start and track a new process."""
        if self.is_running(process_key):
            logger.warning(f"Process '{process_key}' is already running. Ignoring request.")
            return

        logger.info(f"Starting process '{process_key}' with command: {' '.join(command)}")
        try:
            # Set up the environment for the subprocess.
            # `PYTHONPATH` ensures the app's root is in the path.
            # `PYTHONUNBUFFERED` ensures logs appear in real-time.
            env = os.environ.copy()
            env['PYTHONPATH'] = str(config.project_root) + os.pathsep + env.get('PYTHONPATH', '')
            env['PYTHONUNBUFFERED'] = '1'

            process = subprocess.Popen(
                command,
                stdout=sys.stdout, # Redirect stdout to the main process's stdout
                stderr=sys.stderr, # Redirect stderr to the main process's stderr
                env=env,
                cwd=config.project_root
            )
            self.processes[process_key] = process
        except Exception as e:
            logger.error(f"Failed to start process '{process_key}'.", exc_info=True)
            raise ProcessError(f"Could not start the '{process_key}' background process.") from e

    def start_scan(self, mode: ScanMode) -> None:
        """
        Starts the main clustering scan process in the background.

        Args:
            mode: The scan mode, either 'incremental' or 'full'.
        
        Raises:
            ProcessError: If the subprocess fails to start.
        """
        command = self._get_base_command() + [f"--mode={mode}"]
        self._start_process('scan', command)

    def start_enrichment(self, suggestion_id: int) -> None:
        """
        Starts the VLM enrichment process for a single suggestion.

        Args:
            suggestion_id: The ID of the suggestion to enrich.

        Raises:
            ProcessError: If the subprocess fails to start.
        """
        command = self._get_base_command() + [f"--enrich-id={suggestion_id}"]
        self._start_process(f"enrich_{suggestion_id}", command)

    def is_running(self, process_key: str) -> bool:
        """
        Checks if a specific process is currently running. Also cleans up finished processes.

        Args:
            process_key: The unique key for the process (e.g., 'scan', 'enrich_123').

        Returns:
            True if the process is running, False otherwise.
        """
        if process_key not in self.processes:
            return False
        
        process = self.processes[process_key]
        # poll() returns None if the process is still running.
        if process.poll() is None:
            return True
        else:
            # The process has finished, so we can remove it.
            logger.info(f"Process '{process_key}' finished with exit code {process.returncode}. Cleaning up.")
            del self.processes[process_key]
            return False

    def get_running_process_keys(self) -> list[str]:
        """Returns a list of keys for all currently running processes."""
        # This list comprehension also implicitly cleans up finished processes.
        return [key for key in list(self.processes.keys()) if self.is_running(key)]

    def _register_cleanup_handlers(self) -> None:
        """Register signal handlers and cleanup functions for graceful shutdown."""
        # Register cleanup on normal program exit
        atexit.register(self._cleanup_all_processes)
        
        # Signal handlers can only be registered in the main thread
        # Skip signal registration if not in main thread (e.g., when imported by Streamlit)
        try:
            if hasattr(signal, 'SIGTERM'):
                signal.signal(signal.SIGTERM, self._signal_cleanup_handler)
            if hasattr(signal, 'SIGINT'):
                signal.signal(signal.SIGINT, self._signal_cleanup_handler)
        except ValueError as e:
            if "signal only works in main thread" in str(e):
                logger.debug("Skipping signal handler registration - not in main thread")
            else:
                raise
    
    def _signal_cleanup_handler(self, signum: int, frame) -> None:
        """Signal handler that performs cleanup and exits gracefully."""
        logger.info(f"Received signal {signum}, cleaning up processes...")
        self._cleanup_all_processes()
        sys.exit(0)
    
    def _cleanup_all_processes(self) -> None:
        """Terminate all running processes gracefully."""
        if not self.processes:
            return
        
        logger.info(f"Cleaning up {len(self.processes)} background processes...")
        
        for process_key, process in self.processes.items():
            try:
                if process.poll() is None:  # Process is still running
                    logger.info(f"Terminating process '{process_key}' (PID: {process.pid})")
                    
                    # Try graceful termination first
                    process.terminate()
                    
                    # Wait up to 5 seconds for graceful shutdown
                    try:
                        process.wait(timeout=5)
                        logger.debug(f"Process '{process_key}' terminated gracefully")
                    except subprocess.TimeoutExpired:
                        # Force kill if graceful termination fails
                        logger.warning(f"Process '{process_key}' did not terminate gracefully, force killing...")
                        process.kill()
                        process.wait()  # Wait for the kill to complete
                        
            except Exception as e:
                logger.error(f"Error cleaning up process '{process_key}': {e}")
        
        # Clear the processes dictionary
        self.processes.clear()
        logger.info("Process cleanup completed")

# Singleton instance
process_service = ProcessService()