"""
Migration: Create column_views table for custom column views.

Adds support for user-defined views with different column visibility,
order, width, sort, filter, and group configurations.

Run方式:
    cd server && python -m migrations.0025_create_column_views
    cd server && python -m migrations.0025_create_column_views down
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_db


def up():
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS column_views (
                id SERIAL PRIMARY KEY,
                page_id VARCHAR(100) NOT NULL REFERENCES page_configs(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL,
                is_public BOOLEAN DEFAULT false,
                creator_id VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
                is_default BOOLEAN DEFAULT false,
                columns JSONB NOT NULL DEFAULT '[]'::jsonb,
                sort_config JSONB DEFAULT '[]'::jsonb,
                filter_config JSONB DEFAULT '[]'::jsonb,
                group_config JSONB DEFAULT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX idx_column_views_page ON column_views(page_id);
            CREATE INDEX idx_column_views_creator ON column_views(creator_id);
            CREATE INDEX idx_column_views_public ON column_views(is_public) WHERE is_public = true;
        """)

        conn.commit()
        print("Migration 0025: column_views table created successfully")


def down():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS column_views CASCADE")
        conn.commit()
        print("Migration 0025: column_views table dropped")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'down':
        down()
    else:
        up()
