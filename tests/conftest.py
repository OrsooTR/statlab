"""Shared test fixtures: ensure the SQLite schema exists before any test runs."""
import pytest

from app.core.database import init_db


@pytest.fixture(scope="session", autouse=True)
def _init_database():
    init_db()
    yield
