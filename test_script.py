#!/usr/bin/env python3
"""Simple test script to verify Docker Python execution"""

print("TEST SCRIPT: Starting...", flush=True)
import sys
print(f"TEST SCRIPT: Python version: {sys.version}", flush=True)
print(f"TEST SCRIPT: Executable: {sys.executable}", flush=True)
print(f"TEST SCRIPT: Working directory: {sys.path[0] if sys.path else 'unknown'}", flush=True)
print("TEST SCRIPT: Completed successfully!", flush=True)