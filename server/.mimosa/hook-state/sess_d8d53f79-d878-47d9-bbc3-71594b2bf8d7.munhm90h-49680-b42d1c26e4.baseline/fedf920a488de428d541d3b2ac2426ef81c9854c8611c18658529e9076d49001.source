# -*- coding: utf-8 -*-
"""会话内容搜索物化（大数据量优化 §1.2，2026-09-28）。

searchSessions 原实现对本会话全部消息做 jsonb_array_elements 展开后逐
text 片段 ILIKE——关联子查询 + 无索引可用，消息量上十万后搜索从秒级劣化
到分钟级。

方案：ai_chat_messages 加 search_text 物化列（content 里 type='text' 的
片段按行拼接），BEFORE INSERT OR UPDATE 触发器自动维护（消息写入点有
ai_chat/kefu/batch_engine/admin 等六处，触发器一次覆盖所有路径，避免
Python 侧漏改），gin_trgm 索引加速 ILIKE，存量行分批回填。

searchSessions 的 jsonb 展开谓词随之替换为 search_text ILIKE。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "ALTER TABLE ai_chat_messages ADD COLUMN IF NOT EXISTS search_text TEXT")
        cur.execute("""
            CREATE OR REPLACE FUNCTION ai_chat_messages_search_text_fn()
            RETURNS trigger AS $fn$
            BEGIN
              NEW.search_text := COALESCE(
                (SELECT string_agg(p->>'text', chr(10))
                   FROM jsonb_array_elements(NEW.content) p
                  WHERE p->>'type' = 'text' AND p->>'text' IS NOT NULL), '');
              RETURN NEW;
            END;
            $fn$ LANGUAGE plpgsql
        """)
        cur.execute(
            "DROP TRIGGER IF EXISTS trg_ai_chat_messages_search_text "
            "ON ai_chat_messages")
        cur.execute("""
            CREATE TRIGGER trg_ai_chat_messages_search_text
              BEFORE INSERT OR UPDATE OF content ON ai_chat_messages
              FOR EACH ROW EXECUTE FUNCTION ai_chat_messages_search_text_fn()
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_search_trgm
              ON ai_chat_messages USING gin (search_text gin_trgm_ops)
        """)
        # 分页窗口排序索引（§1.1）：ORDER BY seq DESC LIMIT 走 (session_id, seq)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_chat_messages_session_seq
              ON ai_chat_messages (session_id, seq)
        """)
        conn.commit()

        # 存量回填：分批（1000 行/批）避免大表长事务锁
        while True:
            cur.execute("""
                UPDATE ai_chat_messages SET search_text = COALESCE(
                    (SELECT string_agg(p->>'text', chr(10))
                       FROM jsonb_array_elements(content) p
                      WHERE p->>'type' = 'text' AND p->>'text' IS NOT NULL), '')
                WHERE id IN (
                    SELECT id FROM ai_chat_messages WHERE search_text IS NULL
                    LIMIT 1000)
            """)
            if cur.rowcount == 0:
                break
        conn.commit()
    print("message search_text materialization ready.")


if __name__ == "__main__":
    run()
