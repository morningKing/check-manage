import sys, os
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.ai_query as aq

def _db(row):
    conn = MagicMock(); cur = MagicMock()
    cur.fetchone.return_value = row
    conn.cursor.return_value = cur
    @contextmanager
    def fake():
        yield conn
    return fake

def test_get_ai_settings_exposes_mem0_fields():
    # row order must match the SELECT in get_ai_settings
    row = (True, 'sk', 'https://x/v1/chat/completions', 'qwen-plus', 30, 1024, None, True, 'text-embedding-v3')
    with patch.object(aq, 'get_db', _db(row)):
        cfg = aq.get_ai_settings()
    assert cfg['mem0Enabled'] is True
    assert cfg['embeddingModel'] == 'text-embedding-v3'
