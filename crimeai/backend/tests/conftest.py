"""Shared test configuration. Database fixtures live in tests/integration/conftest.py."""
import os

os.environ.setdefault("APP_ENV", "test")
