"""ai_scan_engine.sweep_orphans —— 扫描孤儿恢复的唯一入口（此前零测试）。

语义：对每个扫描任务，把状态字段仍为 running_value、但已不存在存活
（pending/running）子会话的记录，重置回 pending_value。
"""
import sys, os
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from utils.ai_scan_engine import sweep_orphans


def _mock_db():
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = lambda s, *a: None
    conn.cursor.return_value.execute = cur.execute
    conn.cursor.return_value.fetchone = cur.fetchone
    conn.cursor.return_value.fetchall = cur.fetchall
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None

    @contextmanager
    def fake():
        yield conn
    return fake, cur, conn


TASK = ('task-1', 'orders', 'main', '审核状态', '处理中', '待处理')


def test_no_tasks_no_updates():
    fake, cur, conn = _mock_db()
    cur.fetchall.return_value = []
    with patch('utils.ai_scan_engine.get_db', fake):
        sweep_orphans()
    updates = [c for c in cur.execute.call_args_list
               if 'UPDATE dynamic_data' in str(c.args[0])]
    assert updates == []


def test_resets_running_rows_without_live_children():
    fake, cur, conn = _mock_db()
    cur.fetchall.return_value = [TASK]
    with patch('utils.ai_scan_engine.get_db', fake):
        sweep_orphans()
    updates = [c for c in cur.execute.call_args_list
               if 'UPDATE dynamic_data' in str(c.args[0])]
    assert len(updates) == 1
    sql = updates[0].args[0]
    # 单条参数化 jsonb_set：running→pending，且仅当无存活子会话
    assert 'jsonb_set' in sql
    assert "NOT EXISTS" in sql and 'ai_chat_sessions' in sql
    assert "status IN ('pending','running')" in sql
    params = updates[0].args[1]
    assert params == ('审核状态', '待处理', 'orders', 'main', '审核状态', '处理中', 'task-1')
    conn.commit.assert_called_once()


def test_pending_value_none_defaults_to_empty_string():
    fake, cur, conn = _mock_db()
    task = ('task-2', 'orders', 'main', '状态', '处理中', None)
    cur.fetchall.return_value = [task]
    with patch('utils.ai_scan_engine.get_db', fake):
        sweep_orphans()
    upd = [c for c in cur.execute.call_args_list
           if 'UPDATE dynamic_data' in str(c.args[0])][0]
    assert upd.args[1][1] == ''                   # NULL → ''（清空该字段）
    assert conn.commit.call_count == 1


def test_each_task_commits_independently():
    """单任务失败不吞掉其余任务的清扫。"""
    fake, cur, conn = _mock_db()
    cur.fetchall.return_value = [TASK, ('task-3', 'ops', 'main', 'st', 'r', 'p')]
    with patch('utils.ai_scan_engine.get_db', fake):
        sweep_orphans()
    assert conn.commit.call_count == 2
