# -*- coding: utf-8 -*-
"""幂等迁移：智能写作附件表 + 导入种子正文列。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS se_attachments ("
            " id TEXT PRIMARY KEY,"
            " document_id TEXT NOT NULL,"
            " owner_user_id TEXT NOT NULL,"
            " filename TEXT NOT NULL,"
            " mime VARCHAR(100) NOT NULL,"
            " size BIGINT NOT NULL,"
            " stored_path TEXT NOT NULL,"
            " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_se_attachments_document "
                    "ON se_attachments (document_id)")
        cur.execute("ALTER TABLE se_documents ADD COLUMN IF NOT EXISTS seed_content_html TEXT")
        conn.commit()
    print("suggest editor attachments/seed migration ready.")


if __name__ == "__main__":
    run()
