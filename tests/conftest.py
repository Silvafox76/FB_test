"""Shared fixtures.

`db_conn` is a pipeline connection whose work is rolled back, so a test that
writes model_calls rows leaves none behind.
"""

from __future__ import annotations

import os

import psycopg
import pytest


@pytest.fixture
def db_conn():
    url = os.environ.get("DATABASE_URL_PIPELINE")
    if not url:
        pytest.fail("DATABASE_URL_PIPELINE is not set; run 'make up' before 'make test'")
    with psycopg.connect(url) as conn:
        yield conn
        conn.rollback()
