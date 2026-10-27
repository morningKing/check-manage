"""Tests for AI chat session search (GET /ai/chat/sessions/search).

Two modes（批任务搜索 Spec §5 + 2026-10-27 全量搜索扩展）:

- Default (no batchId): regular sessions (active/closed) by title OR message
  text, PLUS batch coverage — 批任务本体按名称命中（hitType='batch'，带进度，
  排在会话命中之前），批子会话跨全部批次按白名单状态搜标题/输入文件/预览/
  消息内容（isBatchChild=True，带批次上下文与消息锚点）。
- batchId given: only that batch's children, same child semantics.
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


def _db_seq(fetchall_seq):
    """fetchall 按查询次序返回不同结果——默认全量搜索现在依次发两条查询
    （批任务名命中 → 合并会话查询），单结果 mock 会让两条查询拿到同一份行。"""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.side_effect = [list(rows) for rows in fetchall_seq]
    conn.cursor.return_value = cur

    @contextmanager
    def fake():
        yield conn

    return fake, cur


# Row shape returned by the merged default-search query (15 cols, 同批内搜索):
# (id, title, last_active_at, status, batch_id, batch_input_file, batch_seq,
#  batch_name, last_message_preview, title_match, file_match, preview_match,
#  hit_content, hit_msg_id, hit_seq)
def _row(sid='s1', title='会话标题', status='active', title_match=False,
         hit_content=None, ts=None, batch_id=None, batch_input_file=None,
         hit_msg_id=None, hit_seq=None):
    return (sid, title, ts, status, batch_id, batch_input_file, None, None,
            None, title_match, False, False, hit_content, hit_msg_id, hit_seq)


# Row shape returned by the batch-scoped query (15 cols):
# (id, title, last_active_at, status, batch_id, batch_input_file, batch_seq,
#  batch_name, last_message_preview, title_match, file_match, preview_match,
#  hit_content, hit_msg_id, hit_seq)
def _batch_row(sid='b1', title=None, status='completed', batch_seq=1,
               batch_name='季度报告分析', input_file='uploads/report-001.pdf',
               preview=None, title_match=False, file_match=False,
               preview_match=False, hit_content=None, ts=None,
               hit_msg_id=None, hit_seq=None):
    return (sid, title, ts, status, 'batch-123', input_file, batch_seq,
            batch_name, preview, title_match, file_match, preview_match,
            hit_content, hit_msg_id, hit_seq)


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
    fake, cur = _db_seq([[], []])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=auth', headers=_h('user-42'))
    sql = ' '.join(str(c.args[0]) for c in cur.execute.call_args_list)
    # Same regular-session scope as the list endpoint: user filter + status set
    assert 'user_id' in sql
    assert "status IN ('active', 'closed')" in sql
    # The query term is parameterised (no string interpolation of user input)
    params = [list(c.args[1].values()) if isinstance(c.args[1], dict) else list(c.args[1])
              for c in cur.execute.call_args_list]
    flat = [p for ps in params for p in ps]
    # patterns wrap the term as %auth%; user id is passed bare
    assert any(isinstance(p, str) and 'auth' in p for p in flat)
    assert 'user-42' in flat


def test_search_matches_title_and_content():
    """The WHERE clause must match either the title OR the materialized
    search_text（大数据量优化 §1.2：jsonb 展开换物化列 + trgm 索引）。"""
    fake, cur = _db_seq([[], []])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=jwt', headers=_h())
    sql = ' '.join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert 'ILIKE' in sql
    # 物化列谓词（触发器维护，gin_trgm 索引加速）
    assert 'search_text ILIKE' in sql
    assert 'jsonb_array_elements' not in sql


def test_search_like_wildcards_are_escaped():
    """% and _ in user input must be escaped so they act as literals."""
    fake, cur = _db_seq([[], []])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=a%25b', headers=_h())  # q = a%b
    for c in cur.execute.call_args_list:
        params = c.args[1]
        values = list(params.values()) if isinstance(params, dict) else params
        assert any('\\%' in p for p in values if isinstance(p, str)), \
            f'wildcard not escaped in {c.args[0]!r}'


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------

def test_search_title_hit_response():
    ts = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    rows = [_row(sid='s1', title='JWT 鉴权重构', status='active',
                 title_match=True, hit_content=None, ts=ts)]
    fake, _ = _db_seq([[], rows])
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
    assert hit['hitType'] == 'session'


def test_search_content_hit_builds_snippet():
    content = [{'type': 'text', 'text': '我们来讨论一下如何用 JWT 替换 session 鉴权，' * 3}]
    rows = [_row(sid='s2', title='鉴权讨论', status='active',
                 title_match=False, hit_content=content)]
    fake, _ = _db_seq([[], rows])
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
    fake, _ = _db_seq([[], rows])
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
    assert 'search_text ILIKE' in sql
    assert 'jsonb_array_elements' not in sql


def test_global_search_not_pinned_to_single_batch():
    """全量搜索不限定单个 batchId（区别于批内范围搜索），普通会话范围保持
    active/closed 不变；批子会话分支走 JOIN 本人批次 + 白名单状态。"""
    fake, cur = _db_seq([[], []])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=jwt', headers=_h())
    sql = ' '.join(str(c.args[0]) for c in cur.execute.call_args_list)
    assert "status IN ('active', 'closed')" in sql
    # 批子会话分支：JOIN 批次限定本人，但不钉死单个批次
    assert 'LEFT JOIN ai_chat_batches b ON b.id = s.batch_id AND b.user_id' in sql
    assert 's.status IN %(statuses)s' in sql
    assert 's.batch_id = %(bid)s' not in sql


# ---------------------------------------------------------------------------
# 全量搜索扩展（2026-10-27）：默认搜索覆盖批任务本体 + 跨批子会话
# ---------------------------------------------------------------------------

def test_global_search_queries_batch_names():
    """批任务本体按名称命中：独立小查询，限定本人批次。"""
    fake, cur = _db_seq([[], []])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=季度', headers=_h('user-42'))
    batch_calls = [c for c in cur.execute.call_args_list
                   if 'FROM ai_chat_batches' in str(c.args[0])]
    assert len(batch_calls) == 1
    sql = str(batch_calls[0].args[0])
    assert 'b.name ILIKE' in sql
    assert batch_calls[0].args[1]['uid'] == 'user-42'
    assert batch_calls[0].args[1]['limit'] <= 50


def test_global_search_also_matches_batch_children():
    """批子会话跨全部批次：命中字段同批内搜索（标题/输入文件/预览/内容）。"""
    from routes.ai_chat import BATCH_CHILD_SEARCH_STATUSES
    fake, cur = _db_seq([[], []])
    with patch('routes.ai_chat.get_db', fake):
        _client().get('/ai/chat/sessions/search?q=风险', headers=_h('user-42'))
    session_calls = [c for c in cur.execute.call_args_list
                     if 'FROM ai_chat_sessions' in str(c.args[0])]
    assert len(session_calls) == 1
    sql = str(session_calls[0].args[0])
    assert 's.title ILIKE' in sql
    assert 's.batch_input_file ILIKE' in sql
    assert 's.last_message_preview ILIKE' in sql
    assert 'search_text ILIKE' in sql
    assert 'jsonb_array_elements' not in sql
    params = session_calls[0].args[1]
    assert params['uid'] == 'user-42'
    assert params['statuses'] == BATCH_CHILD_SEARCH_STATUSES


def test_global_search_batch_hit_response_first():
    """批任务本体命中：hitType='batch' + 进度字段，排在会话命中之前。"""
    ts = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    batch_rows = [('batch-9', '季度报告分析', 'running', 12, 5, 1, ts)]
    session_rows = [_row(sid='s1', title='JWT 鉴权重构', title_match=True, ts=ts)]
    fake, _ = _db_seq([batch_rows, session_rows])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=季度', headers=_h())
    hits = r.get_json()['sessions']
    assert hits[0]['hitType'] == 'batch'
    assert hits[0]['id'] == 'batch-9'
    assert hits[0]['title'] == '季度报告分析'
    assert hits[0]['status'] == 'running'
    assert hits[0]['total'] == 12
    assert hits[0]['done'] == 5
    assert hits[0]['failed'] == 1
    assert hits[0]['matchField'] == 'title'
    assert hits[0]['snippet'] == ''
    assert hits[0]['isBatchChild'] is False
    assert hits[0]['messageId'] is None
    # 会话命中排在其后，显式 hitType='session'
    assert hits[1]['id'] == 's1'
    assert hits[1]['hitType'] == 'session'
    assert hits[1]['matchField'] == 'title'


def test_global_search_batch_hit_without_session_hits():
    """批名命中但零会话命中时，批任务命中仍必须返回（拼装独立于会话行循环）。"""
    ts = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    batch_rows = [('batch-9', '季度报告分析', 'failed', 3, 0, 3, ts)]
    fake, _ = _db_seq([batch_rows, []])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=季度', headers=_h())
    hits = r.get_json()['sessions']
    assert len(hits) == 1
    assert hits[0]['hitType'] == 'batch'
    assert hits[0]['id'] == 'batch-9'
    assert hits[0]['status'] == 'failed'
    assert hits[0]['failed'] == 3


def test_global_search_child_hit_carries_batch_context():
    """全量搜索命中的批子会话带批次上下文 + 消息锚点，isBatchChild=True。"""
    session_rows = [_batch_row(sid='b1', title='report-001.pdf', status='failed',
                               batch_seq=3,
                               hit_content='[{"type":"text","text":"存在风险"}]',
                               hit_msg_id='msg_1', hit_seq=9)]
    fake, _ = _db_seq([[], session_rows])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=风险', headers=_h())
    hit = r.get_json()['sessions'][0]
    assert hit['isBatchChild'] is True
    assert hit['batchId'] == 'batch-123'
    assert hit['batchName'] == '季度报告分析'
    assert hit['batchSeq'] == 3
    assert hit['inputFileName'] == 'report-001.pdf'
    assert hit['matchField'] == 'content'
    assert hit['messageId'] == 'msg_1'
    assert hit['seq'] == 9


def test_global_search_combined_hits_capped_at_50():
    """批任务命中 + 会话命中合并后总数不超 50。"""
    batch_rows = [(f'batch-{i}', f'批任务{i}', 'completed', 1, 1, 0, None)
                  for i in range(30)]
    session_rows = [_row(sid=f's{i}', title_match=True) for i in range(30)]
    fake, _ = _db_seq([batch_rows, session_rows])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=x', headers=_h())
    assert len(r.get_json()['sessions']) == 50


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


# ---------------------------------------------------------------------------
# 消息锚点（spec 2026-10-25-session-outline §4.2）：仅 content 命中携带
# ---------------------------------------------------------------------------

def test_content_hit_carries_message_anchor():
    row = _row(sid='s1', title_match=False,
               hit_content='[{"type":"text","text":"部门汇总结果"}]',
               hit_msg_id='msg_hit1', hit_seq=17)
    fake, _ = _db_seq([[], [row]])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=汇总', headers=_h())
    hit = r.get_json()['sessions'][0]
    assert hit['matchField'] == 'content'
    assert hit['messageId'] == 'msg_hit1'
    assert hit['seq'] == 17


def test_title_hit_has_no_message_anchor():
    row = _row(sid='s1', title_match=True)
    fake, _ = _db_seq([[], [row]])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=标题', headers=_h())
    hit = r.get_json()['sessions'][0]
    assert hit['matchField'] == 'title'
    assert hit['messageId'] is None
    assert hit['seq'] is None


def test_batch_content_hit_carries_message_anchor():
    row = _batch_row(title_match=False, file_match=False, preview_match=False,
                     hit_content='[{"type":"text","text":"批内命中"}]',
                     hit_msg_id='msg_b1', hit_seq=9)
    fake, _ = _db([row])
    with patch('routes.ai_chat.get_db', fake):
        r = _client().get('/ai/chat/sessions/search?q=命中&batchId=batch-123',
                          headers=_h())
    hit = r.get_json()['sessions'][0]
    assert hit['matchField'] == 'content'
    assert hit['messageId'] == 'msg_b1'
    assert hit['seq'] == 9
