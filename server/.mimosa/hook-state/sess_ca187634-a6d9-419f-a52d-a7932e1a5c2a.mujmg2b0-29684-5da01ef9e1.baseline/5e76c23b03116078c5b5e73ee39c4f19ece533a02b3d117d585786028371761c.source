"""
Test version_snapshots migration: collection field addition
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

def test_version_snapshots_has_collection_field():
    """Test that version_snapshots table has collection field after migration"""
    with get_db() as conn:
        cur = conn.cursor()

        # Check if collection column exists
        cur.execute(
            'SELECT column_name FROM information_schema.columns '
            'WHERE table_name = %s AND column_name = %s',
            ('version_snapshots', 'collection')
        )

        result = cur.fetchone()
        assert result is not None, 'collection column should exist in version_snapshots'

if __name__ == '__main__':
    test_version_snapshots_has_collection_field()
    print('\nTest passed!')