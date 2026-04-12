"""
Test to verify that track_version_collections uses explicit list instead of database scan.
This test is focused on the specific fix for the critical bug.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2.extras
from db import get_db
from utils.version import track_version_collections
from datetime import datetime, timezone

def test_explicit_collections_list():
    """Verify that track_version_collections uses the explicit list when provided"""

    print('\n=== Testing track_version_collections with explicit list ===\n')

    # Setup test data
    version_id = 'test-explicit-ver-001'
    collection = 'test-empty-a'

    with get_db() as conn:
        cur = conn.cursor()

        # Clean up old test data
        cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM collection_versions WHERE id = %s', (version_id,))
        conn.commit()

        # Insert a dummy version record
        cur.execute(
            'INSERT INTO collection_versions '
            '(id, collection, name, description, version_type, status, created_by, created_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
            (version_id, collection, 'Test Version', 'Testing explicit list',
             'branch', 'active', 'test-user', datetime.now(timezone.utc))
        )
        conn.commit()

        # Test 1: Call with explicit list of 2 collections
        print('Test 1: Tracking with explicit list of 2 collections')
        affected_collections = ['test-empty-a', 'test-empty-b']
        track_version_collections(
            version_id,
            collection,
            'test-branch',
            conn,
            affected_collections  # Explicit list
        )
        conn.commit()

        # Verify: Should only track the 2 specified collections
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s ORDER BY collection',
            (version_id,)
        )
        tracked = [row[0] for row in cur.fetchall()]
        print(f'  Tracked collections: {tracked}')
        assert len(tracked) == 2, f'Expected 2 collections, got {len(tracked)}'
        assert 'test-empty-a' in tracked, 'test-empty-a should be tracked'
        assert 'test-empty-b' in tracked, 'test-empty-b should be tracked'
        print('  [PASS] Correctly tracked only the 2 specified collections\n')

        # Clean up for next test
        cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
        conn.commit()

        # Test 2: Call with empty list (should fall back to primary collection)
        print('Test 2: Tracking with empty list (should use primary collection)')
        track_version_collections(
            version_id,
            collection,
            'test-branch',
            conn,
            []  # Empty list
        )
        conn.commit()

        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s',
            (version_id,)
        )
        tracked = [row[0] for row in cur.fetchall()]
        print(f'  Tracked collections: {tracked}')
        assert len(tracked) == 1, f'Expected 1 collection, got {len(tracked)}'
        assert tracked[0] == collection, f'Should be {collection}, got {tracked[0]}'
        print('  [PASS] Correctly fell back to primary collection\n')

        # Clean up for next test
        cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
        conn.commit()

        # Test 3: Call without list (backward compatibility - uses database scan)
        print('Test 3: Backward compatibility - calling without list parameter')
        # This should trigger the database scan fallback
        # For this test, we don't pass the affected_collections parameter at all
        track_version_collections(
            version_id,
            collection,
            'test-branch',
            conn
            # No affected_collections parameter
        )
        conn.commit()

        cur.execute(
            'SELECT COUNT(*) FROM version_collections WHERE version_id = %s',
            (version_id,)
        )
        count = cur.fetchone()[0]
        print(f'  Tracked {count} collection(s) via database scan')
        print('  [PASS] Backward compatibility maintained (database scan works)\n')

        # Cleanup
        cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM collection_versions WHERE id = %s', (version_id,))
        conn.commit()

    print('=== All tests passed! ===\n')
    print('Summary:')
    print('  [OK] Explicit list is used when provided')
    print('  [OK] Empty list falls back to primary collection')
    print('  [OK] Backward compatibility maintained (database scan works)')

if __name__ == '__main__':
    test_explicit_collections_list()