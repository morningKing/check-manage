# -*- coding: utf-8 -*-
"""幂等迁移：O2 技能进化候选区（ai_skill_candidates）。

LLM 诊断产物只进候选区，永不直接写定义文件（第 8 章安全边界②候选与正式
隔离）。edits=[{old_str,new_str,reason}] 最小 diff；无法表达为 diff 时整块
降级存 fallback_content（UI 显式标注）。promoted_version_id 关联发布产生
的 ai_skill_def_versions 行，回滚复用现有 rollback 端点。edits_hash 是
canonical edits JSON 的 sha256（R2 去重键）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db

def run():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_skill_candidates (
                id                  TEXT PRIMARY KEY,
                def_kind            VARCHAR(30) NOT NULL,
                def_name            VARCHAR(200) NOT NULL,
                base_content_hash   VARCHAR(64) NOT NULL,
                edits               JSONB NOT NULL DEFAULT '[]'::jsonb,
                edits_hash          VARCHAR(64),
                fallback_content    TEXT,
                source              JSONB NOT NULL DEFAULT '{}'::jsonb,
                validation          JSONB,
                status              VARCHAR(20) NOT NULL DEFAULT 'pending',
                created_by          VARCHAR(60) NOT NULL DEFAULT 'fit-diagnose',
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                decided_at          TIMESTAMPTZ,
                decided_by          VARCHAR(60),
                promoted_version_id TEXT REFERENCES ai_skill_def_versions(id)
            )""")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_skill_cand_lookup
                ON ai_skill_candidates(def_kind, def_name, status)""")
        # R2 去重：同一 (def, 基线, diff) 只允许一个未决候选。
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_cand_active
                ON ai_skill_candidates(def_kind, def_name, base_content_hash, edits_hash)
                WHERE status IN ('pending', 'validated')""")
        conn.commit()
    print("skill candidates migration ready.")

if __name__ == "__main__":
    run()
