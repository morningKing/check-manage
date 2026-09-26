# -*- coding: utf-8 -*-
"""M9 回修迁移：artifact 去重唯一索引按 owner 隔离 + sending 回收索引。

- 旧 uniq_artifact_sha (sha256, name) 全局去重会让第二个用户命中他人行
  （owner 是别人 → 本人下载 403、列表不可见）。改为 (sha256, name,
  COALESCE(owner_user_id, ''))——同用户去重、跨用户隔离。
- idx_outbox_sending_reclaim：sending 状态的 10 分钟回收谓词补索引，
  避免 M12 回收路径全表扫。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DROP INDEX IF EXISTS uniq_artifact_sha")
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_artifact_sha_owner
              ON artifacts(sha256, name, COALESCE(owner_user_id, ''))
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_outbox_sending_reclaim
              ON ai_delivery_outbox(next_retry_at)
             WHERE status = 'sending'
        """)
        conn.commit()
    print("artifact owner-scope index + outbox reclaim index ready.")


if __name__ == "__main__":
    run()
