# -*- coding: utf-8 -*-
"""mem0 嵌入模型提供方配置（2026-09-29）。

嵌入式模型（embedder）支持两种提供方，可在后台 AI 设置中选择：
- 'api'：原有 API 端点（openai 兼容，DashScope 等）+ embedding_model；
- 'ollama'：本地 Ollama 服务 + embedding_ollama_url + embedding_model
  （摘要 LLM 不受影响，始终走原 API 端点）。

embedding_dims：向量维度，需与所选嵌入模型匹配（如 nomic-embed-text=768、
text-embedding-v3=1024）。**切换提供方/模型后，历史记忆向量的维度可能与
新模型不一致**（chroma collection 维度固定），届时记忆功能会降级 no-op——
需要清空记忆数据（MEM0_STORE_ROOT）后重建。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2
from config import DB_CONFIG

def main():
    conn = psycopg2.connect(**DB_CONFIG); conn.autocommit = True
    cur = conn.cursor()
    # 常量 DDL（列名/默认值均为字面量，无用户输入参与）
    cur.execute("ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS embedding_provider varchar DEFAULT 'api'")
    cur.execute("ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS embedding_ollama_url varchar DEFAULT 'http://localhost:11434'")
    cur.execute("ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS embedding_dims integer DEFAULT 1024")
    print('ai_settings embedding provider columns ensured')
    cur.close(); conn.close()

if __name__ == '__main__':
    main()
