import sys
import os
import pytest
from unittest.mock import MagicMock
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def mock_cursor():
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = None
    return cur


@pytest.fixture
def mock_conn(mock_cursor):
    conn = MagicMock()
    conn.cursor.return_value = mock_cursor
    conn.__enter__ = lambda self: conn
    conn.__exit__ = lambda self, *a: None
    return conn


@pytest.fixture
def fake_db(mock_conn):
    @contextmanager
    def _fake():
        yield mock_conn
    return _fake
