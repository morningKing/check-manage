"""SkillOpt 定义版本正文归档列（2026-10-05）。

ai_skill_def_versions 增加正文与定格时间两列（spec §3）：

  content             主定义文件正文（SKILL.md / agent md 全文，NULL=未归档）
  content_captured_at 正文定格时间（与 first_seen_at「版本首见」语义分离）

全部幂等，可重复执行。历史存量行 content 为 NULL → UI 标「未归档」，
不做伪回填（正文已不存在）。

用法（在 server/ 目录下）：
    python migrations/2026_10_05_def_version_content_archive.py
也可由 init_db.py 在建库流程末尾经 _run_dated_migrations() 调用 run()。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

DDL = [
    "ALTER TABLE ai_skill_def_versions "
    "ADD COLUMN IF NOT EXISTS content TEXT",
    "ALTER TABLE ai_skill_def_versions "
    "ADD COLUMN IF NOT EXISTS content_captured_at TIMESTAMPTZ",
]


def run():
    with get_db() as conn:
        with conn.cursor() as cur:
            for stmt in DDL:
                cur.execute(stmt)
        conn.commit()
    print("def version content archive columns ready "
          "(content/content_captured_at).")


if __name__ == "__main__":
    run()
