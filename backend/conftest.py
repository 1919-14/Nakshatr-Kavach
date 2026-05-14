# backend/conftest.py
"""
Pytest root configuration for NAKSHATRA-KAVACH Layer 1 tests.
Adds the backend/ directory to sys.path so imports resolve correctly.
"""

import sys
from pathlib import Path

# Ensure `backend/` is on the path so `from app.x import y` works in tests
sys.path.insert(0, str(Path(__file__).resolve().parent))
