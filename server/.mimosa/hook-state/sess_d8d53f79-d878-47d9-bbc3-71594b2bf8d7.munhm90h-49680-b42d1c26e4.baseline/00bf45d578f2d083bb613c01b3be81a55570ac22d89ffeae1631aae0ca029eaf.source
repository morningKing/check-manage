"""Admin session search keyword semantics (批任务搜索 Spec §9 管理员一致性).

The admin list keyword must match title / input file / last_message_preview
AND the full `ai_chat_messages.content` text parts — running/failed batch
children whose history lives only in messages must be findable, not just rows
whose last preview happens to contain the term.
"""

import sys
import os
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.session_admin_repo import _build_where, admin_list_sessions_v2


def test_keyword_matches_message_content():
    where, params = _build_where(None, None, None, '供应链风险', None)
    assert 'ai_chat_messages' in where
    assert 'jsonb_array_elements' in where
    assert "p->>'type' = 'text'" in where
    assert "p->>'text' ILIKE %s" in where
    # 4 keyword patterns: title + file + preview + message text
    kws = [p for p in params if p == '%供应链风险%']
    assert len(kws) == 4


def test_keyword_no_message_search_when_absent():
    where, params = _build_where('running', 'batch', None, None, 'batch-1')
    assert 'ai_chat_messages' not in where
    assert params == ('running', 'batch', 'batch-1')


def test_batch_id_filter_still_exact():
    where, params = _build_where(None, None, None, 'risk', 'batch-9')
    assert 's.batch_id = %s' in where
    assert 'batch-9' in params


def test_admin_list_v2_query_carries_message_exists():
    """The repo query itself must carry the EXISTS clause (not just _build_where)."""
    executed = []

    class Cur:
        def execute(self, sql, params=None):
            executed.append((sql, params))

        def fetchone(self):
            return {'n': 0}

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    conn = MagicMock()
    conn.cursor.return_value = Cur()

    @contextmanager
    def fake_db():
        yield conn

    with patch('utils.session_admin_repo.get_db', fake_db):
        admin_list_sessions_v2(keyword='风险', batch_id='batch-1')

    assert executed, 'expected two queries (count + page)'
    for sql, params in executed:
        assert 'ai_chat_messages' in sql
        assert 's.batch_id = %s' in sql
        assert params.count('%风险%') == 4
