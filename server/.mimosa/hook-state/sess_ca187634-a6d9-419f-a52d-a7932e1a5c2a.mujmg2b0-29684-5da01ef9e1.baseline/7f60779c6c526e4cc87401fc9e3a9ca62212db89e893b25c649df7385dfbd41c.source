"""
Migration: Add collection field to version_snapshots and backfill existing data
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

def migrate_version_snapshots_collection():
    """Add collection column and backfill from version metadata"""
    with get_db() as conn:
        cur = conn.cursor()

        print('Step 1: Adding collection column...')
        cur.execute(
            'ALTER TABLE version_snapshots ADD COLUMN collection VARCHAR(200)'
        )

        print('Step 2: Backfilling existing snapshots...')
        # Infer collection from version metadata
        cur.execute(
            'SELECT vs.version_id, vs.record_id, cv.collection '
            'FROM version_snapshots vs '
            'JOIN collection_versions cv ON vs.version_id = cv.id '
            'WHERE vs.collection IS NULL'
        )
        updates = cur.fetchall()

        for version_id, record_id, collection in updates:
            cur.execute(
                'UPDATE version_snapshots SET collection = %s '
                'WHERE version_id = %s AND record_id = %s',
                (collection, version_id, record_id),
            )

        print(f'  Updated {len(updates)} snapshot records')

        print('Step 3: Updating PRIMARY KEY...')
        cur.execute(
            'ALTER TABLE version_snapshots DROP CONSTRAINT version_snapshots_pkey'
        )
        cur.execute(
            'ALTER TABLE version_snapshots ADD PRIMARY KEY (version_id, collection, record_id)'
        )

        print('Step 4: Creating composite index...')
        cur.execute(
            'CREATE INDEX idx_vs_version_collection ON version_snapshots(version_id, collection)'
        )

        conn.commit()
        print('Migration completed successfully!')

if __name__ == '__main__':
    migrate_version_snapshots_collection()
    print('Migration completed!')