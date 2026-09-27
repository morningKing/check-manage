"""Tests for AI chat session search (GET /ai/chat/sessions/search).

Searches the current user's regular (active/closed) sessions by title OR by
message text-part content; batch-child sessions (status='pending') are excluded
by the same status filter used by the regular session list.
"""

import sys
import os
from unittest.mock import patch, MagicMock
from contextlib import contextmanager
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auth import create_token


def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def _h(uid='u1', role='developer'):
    return {'Authorization': 'Bearer ' + create_token({'id': uid, 'username': uid, 'role': role})}


def _db(fetchall_rows):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = fetchall_rows
    conn.cursor.return_value = cur

    @contextmanager
    def fake():
        yield conn

    return fake, cur


# Row shape returned by the regular-search query:
# (id, title, last_active_at, status, batch_id, batch_input_file, title_match, hit_content)
def _row(sid='s1', title='会话标题', status='active', title_match=False,
         hit_content=None, ts=None, batch_id=None, batch_input_file=None):
    return (sid, title, ts, status, batch_id, batch_input_file, title_match, hit_content)


# Row shape returned by the batch-scoped query (13 cols):
# (id, title, last_active_at, status, batch_id, batch_input_file, batch_seq,
#  batch_name, last_message_preview, title_match, file_match, preview_match, hit_content)
def _batch_row(sid='b1', title=None, status='completed', batch_seq=1,
               batch_name='季度报告分析', input_file='uploads/report-001.pdf',
               preview=None, title_match=False, file_match=False,
               preview_match=False, hit_content=None, ts=None):
    return (sid, title, ts, status, 'batch-123', input_file, batch_seq,
            batch_name, preview, title_match, file_match, preview_match, hit_content)


# ---------------------------------------------------------------------------
# Auth / validation
# ---------------------------------------------------------------------------

def test_search_requires_auth():
    r = _client().get('/ai/chat/sessions/search?q=hello')
    assert r.status_code in (401, 403)


def test_search_empty_query_returns_empty_list():
    """q='' must not run a full-table scan; return an empty result set."""
    fake, cur = _db([])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=', headers=_h())
    assert r.status_code == 200
    assert r.get_json() == {'sessions': []}
    # No SQL should be issued for an empty query
    cur.execute.assert_not_called()


def test_search_whitespace_query_returns_empty():
    fake, cur = _db([])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=%20%20%20', headers=_h())
    assert r.status_code == 200
    assert r.get_json() == {'sessions': []}


# ---------------------------------------------------------------------------
# SQL / scoping
# ---------------------------------------------------------------------------

def test_search_scopes_by_user_and_active_closed_status():
    fake, cur = _db([])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=auth', headers=_h('user-42'))
    sql = ' '.join(str(c.args[0]) for c in cur.execute.call_args_list)
    # Same regular-session scope as the list endpoint: user filter + status set
    assert 'user_id' in sql
    assert "status IN ('active', 'closed')" in sql
    # No batch_id exclusion clause (batch children are 'pending', already excluded)
    assert 'batch_id IS NOT NULL' not in sql
    # The query term is parameterised (no string interpolation of user input)
    params = list(cur.execute.call_args.args[1])
    # patterns wrap the term as %auth%; user id is passed bare
    assert any(isinstance(p, str) and 'auth' in p for p in params)
    assert 'user-42' in params


def test_search_matches_title_and_content():
    """The WHERE clause must match either the title OR a message text part."""
    fake, cur = _db([])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=jwt', headers=_h())
    sql = ' '.join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert 'ILIKE' in sql
    # message text-part extraction
    assert "jsonb_array_elements" in sql
    assert "->>'type'" in sql
    assert "->>'text'" in sql


def test_search_like_wildcards_are_escaped():
    """% and _ in user input must be escaped so they act as literals."""
    fake, cur = _db([])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=a%25b', headers=_h())  # q = a%b
    params = cur.execute.call_args.args[1]
    patterns = [p for p in params if isinstance(p, str) and ('a%b' in p or '\\%' in p)]
    assert any('\\%' in p for p in patterns), f'wildcard not escaped: {patterns}'


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------

def test_search_title_hit_response():
    ts = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    rows = [_row(sid='s1', title='JWT 鉴权重构', status='active',
                 title_match=True, hit_content=None, ts=ts)]
    fake, _ = _db(rows)
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=jwt', headers=_h())
    assert r.status_code == 200
    data = r.get_json()
    assert len(data['sessions']) == 1
    hit = data['sessions'][0]
    assert hit['id'] == 's1'
    assert hit['title'] == 'JWT 鉴权重构'
    assert hit['matchField'] == 'title'
    assert hit['snippet'] == ''
    assert hit['status'] == 'active'
    assert hit['lastActiveAt'].startswith('2026-09-08T10:00:00')


def test_search_content_hit_builds_snippet():
    content = [{'type': 'text', 'text': '我们来讨论一下如何用 JWT 替换 session 鉴权，' * 3}]
    rows = [_row(sid='s2', title='鉴权讨论', status='active',
                 title_match=False, hit_content=content)]
    fake, _ = _db(rows)
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=JWT', headers=_h())
    hit = r.get_json()['sessions'][0]
    assert hit['matchField'] == 'content'
    assert 'JWT' in hit['snippet']
    # snippet must be a single line (newlines collapsed)
    assert '\n' not in hit['snippet']


def test_search_content_hit_non_text_parts_ignored():
    """Tool / file parts must not contribute to the text snippet."""
    content = [
        {'type': 'tool', 'tool': 'data_query', 'state': {'output': 'JWT' * 10}},
        {'type': 'file', 'name': 'x.csv', 'path': 'uploads/x.csv'},
        {'type': 'text', 'text': '这里没有关键词'},
    ]
    rows = [_row(sid='s3', title='t', hit_content=content)]
    fake, _ = _db(rows)
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=JWT', headers=_h())
    hit = r.get_json()['sessions'][0]
    # JWT only appeared inside a tool part's output -> text snippet has no JWT
    assert 'JWT' not in hit['snippet']


# ---------------------------------------------------------------------------
# Snippet helper (unit)
# ---------------------------------------------------------------------------

def test_snippet_helper_centers_on_keyword():
    from routes.ai_chat import _content_snippet
    blob = '开头' * 30 + '目标关键词' + '结尾' * 30
    content = [{'type': 'text', 'text': blob}]
    snip = _content_snippet(content, '目标关键词', width=10)
    assert '目标关键词' in snip
    assert snip.startswith('…')  # truncated on the left
    assert snip.endswith('…')    # truncated on the right


def test_snippet_helper_empty():
    from routes.ai_chat import _content_snippet
    assert _content_snippet(None, 'x') == ''
    assert _content_snippet([], 'x') == ''
    assert _content_snippet([{'type': 'file'}], 'x') == ''


# ---------------------------------------------------------------------------
# Batch-scoped search (Spec §6): GET /sessions/search?batchId=...
# ---------------------------------------------------------------------------

def test_batch_search_scopes_to_own_batch_and_status_whitelist():
    """The batch branch must narrow by (user, batchId) BEFORE message JSONB
    parsing, join the batch's owner, and use the centralized status whitelist."""
    from routes.ai_chat import BATCH_CHILD_SEARCH_STATUSES
    fake, cur = _db([])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=风险&batchId=batch-123',
                      headers=_h('user-42'))
    sql = ' '.join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert 'JOIN ai_chat_batches b ON b.id = s.batch_id AND b.user_id' in sql
    assert 's.batch_id = %(bid)s' in sql
    assert 's.status IN %(statuses)s' in sql
    # Whitelist is a positive set: deleted/archived can never sneak in.
    assert 'deleted' not in BATCH_CHILD_SEARCH_STATUSES
    assert 'archived' not in BATCH_CHILD_SEARCH_STATUSES
    for st in ('pending', 'running', 'completed', 'failed'):
        assert st in BATCH_CHILD_SEARCH_STATUSES
    params = cur.execute.call_args.args[1]
    assert params['bid'] == 'batch-123'
    assert params['uid'] == 'user-42'
    assert params['statuses'] == BATCH_CHILD_SEARCH_STATUSES


def test_batch_search_matches_title_file_preview_and_content():
    fake, cur = _db([])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=风险&batchId=batch-123', headers=_h())
    sql = ' '.join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert 's.title ILIKE' in sql
    assert 's.batch_input_file ILIKE' in sql
    assert 's.last_message_preview ILIKE' in sql
    assert 'jsonb_array_elements' in sql
    assert "->>'text'" in sql


def test_batch_search_default_mode_unchanged():
    """Without batchId the legacy single-shape query (active/closed only) runs."""
    fake, cur = _db([])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=jwt', headers=_h())
    sql = ' '.join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert "status IN ('active', 'closed')" in sql
    assert 'JOIN ai_chat_batches' not in sql


def test_batch_search_title_hit_response():
    rows = [_batch_row(sid='b1', title='report-001.pdf', status='running',
                       batch_seq=3, title_match=True,
                       input_file='uploads/report-001.pdf')]
    fake, _ = _db(rows)
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=report&batchId=batch-123',
                          headers=_h())
    assert r.status_code == 200
    hit = r.get_json()['sessions'][0]
    assert hit['id'] == 'b1'
    assert hit['matchField'] == 'title'
    assert hit['snippet'] == ''
    assert hit['isBatchChild'] is True
    assert hit['batchId'] == 'batch-123'
    assert hit['batchName'] == '季度报告分析'
    assert hit['batchSeq'] == 3
    assert hit['inputFileName'] == 'report-001.pdf'
    assert hit['status'] == 'running'


def test_batch_search_file_hit_skips_snippet():
    rows = [_batch_row(sid='b2', file_match=True, hit_content=None,
                       input_file='uploads/quarterly-风险报告.pdf')]
    fake, _ = _db(rows)
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=风险&batchId=batch-123',
                          headers=_h())
    hit = r.get_json()['sessions'][0]
    assert hit['matchField'] == 'file'
    assert hit['snippet'] == ''
    assert hit['inputFileName'] == 'quarterly-风险报告.pdf'


def test_batch_search_content_hit_builds_snippet():
    content = [{'type': 'text', 'text': '报告中存在供应链风险，需要评估影响面。'}]
    rows = [_batch_row(sid='b3', title='report-003.pdf', hit_content=content)]
    fake, _ = _db(rows)
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=风险&batchId=batch-123',
                          headers=_h())
    hit = r.get_json()['sessions'][0]
    assert hit['matchField'] == 'content'
    assert '风险' in hit['snippet']
    assert '\n' not in hit['snippet']


def test_batch_search_preview_hit_uses_preview_as_snippet():
    rows = [_batch_row(sid='b4', title='report-004.pdf', preview='结论：存在风险',
                       preview_match=True)]
    fake, _ = _db(rows)
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=风险&batchId=batch-123',
                          headers=_h())
    hit = r.get_json()['sessions'][0]
    assert hit['matchField'] == 'preview'
    assert hit['snippet'] == '结论：存在风险'


def test_batch_search_empty_query_returns_empty_without_sql():
    fake, cur = _db([])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=&batchId=batch-123', headers=_h())
    assert r.get_json() == {'sessions': []}
    cur.execute.assert_not_called()


def test_batch_search_limit_capped_at_50():
    fake, cur = _db([])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=x&batchId=batch-123&limit=999',
                      headers=_h())
    params = cur.execute.call_args.args[1]
    assert params['limit'] <= 50
