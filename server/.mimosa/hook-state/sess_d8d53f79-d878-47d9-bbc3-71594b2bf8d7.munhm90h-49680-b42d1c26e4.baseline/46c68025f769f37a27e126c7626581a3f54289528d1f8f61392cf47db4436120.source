import pytest
from migrate_kefu import migrate_kefu


def _col_exists(cur, table, col):
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name=%s AND column_name=%s", (table, col))
    return cur.fetchone() is not None


def test_migrate_creates_objects(db_conn):
    migrate_kefu(db_conn)
    migrate_kefu(db_conn)  # 幂等：二次执行不报错
    cur = db_conn.cursor()
    cur.execute("SELECT to_regclass('public.kefu_instances')")
    assert cur.fetchone()[0] is not None
    for col in ('kefu_instance_id', 'visitor_id', 'needs_human', 'human_takeover'):
        assert _col_exists(cur, 'ai_chat_sessions', col)
    cur.execute("SELECT default_page_access FROM roles WHERE id='kefu-guest'")
    row = cur.fetchone()
    assert row is not None and row[0] == 'none'
    db_conn.rollback()
