"""Idempotent: add mem0 config columns to ai_settings. Run once per DB."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import psycopg2
from config import DB_CONFIG

DDL = [
    "ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS mem0_enabled boolean DEFAULT false",
    "ALTER TABLE ai_settings ADD COLUMN IF NOT EXISTS embedding_model varchar DEFAULT 'text-embedding-v3'",
]

def main():
    conn = psycopg2.connect(**DB_CONFIG); conn.autocommit = True
    cur = conn.cursor()
    for sql in DDL:
        cur.execute(sql)
    print('ai_settings mem0 columns ensured')
    cur.close(); conn.close()

if __name__ == '__main__':
    main()
