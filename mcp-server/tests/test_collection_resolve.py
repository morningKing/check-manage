"""Unit tests for collection_resolve.resolve_collection (mocked cursor)."""
from unittest.mock import MagicMock


def test_resolves_by_exact_collection_slug():
    cur = MagicMock()
    cur.fetchone.return_value = ('page-inspection-case',)
    from collection_resolve import resolve_collection
    assert resolve_collection(cur, 'inspection-case') == 'inspection-case'


def test_resolves_by_data_menu_display_name():
    cur = MagicMock()
    cur.fetchone.return_value = ('page-inspection-case',)
    from collection_resolve import resolve_collection
    assert resolve_collection(cur, '巡检记录') == 'inspection-case'


def test_returns_none_when_nothing_matches():
    cur = MagicMock()
    cur.fetchone.return_value = None
    from collection_resolve import resolve_collection
    assert resolve_collection(cur, 'ghost') is None


def test_returns_none_for_empty_identifier():
    cur = MagicMock()
    from collection_resolve import resolve_collection
    assert resolve_collection(cur, '') is None
    cur.execute.assert_not_called()
