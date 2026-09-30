from migrate_kefu import migrate_kefu


def _col(cur, table, col):
    cur.execute("SELECT 1 FROM information_schema.columns "
                "WHERE table_name=%s AND column_name=%s", (table, col))
    return cur.fetchone() is not None


def test_migrate_creates_faq_table(db_conn):
    migrate_kefu(db_conn)
    migrate_kefu(db_conn)  # idempotent
    cur = db_conn.cursor()
    cur.execute("SELECT to_regclass('public.kefu_faq_items')")
    assert cur.fetchone()[0] is not None
    for c in ('instance_id', 'question', 'answer', 'category',
              'sort_order', 'click_count', 'enabled'):
        assert _col(cur, 'kefu_faq_items', c)
    db_conn.rollback()
