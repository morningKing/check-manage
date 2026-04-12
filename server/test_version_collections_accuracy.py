"""
Integration test to verify that create_version_snapshot correctly passes explicit collections list.

This test verifies the critical fix:
- create_version_snapshot passes list(all_collections_data.keys()) to track_version_collections
- version_collections table contains ONLY the collections that were actually in the snapshot
- NOT all collections from the entire branch
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2.extras
from db import get_db
from utils.version import create_version_snapshot, delete_version
from datetime import datetime, timezone

def test_version_collections_accuracy():
    """
    Test that version_collections accurately reflects snapshot content.

    Setup:
    - Create 2 empty collections (test-empty-a, test-empty-b) with relations
    - Create a version from test-empty-a
    - Verify that version_collections ONLY contains these 2 collections
    - NOT other unrelated collections from the branch (like special-record, inspection-case, etc.)
    """

    print('\n=== Testing version_collections accuracy ===\n')

    # Setup empty test collections with relations
    collection_a = 'test-empty-a'
    collection_b = 'test-empty-b'
    test_user = 'test_accuracy_user'

    with get_db() as conn:
        cur = conn.cursor()

        # Clean up old test data
        print('Step 1: Cleanup old test data')
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM collection_versions WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM version_snapshots WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM version_relations WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM version_collections WHERE collection IN (%s, %s)', (collection_a, collection_b))
        conn.commit()
        print('[OK] Cleanup complete\n')

        # Create minimal test data in these 2 collections only
        print('Step 2: Create minimal test data in 2 collections')
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-acc-a-001', collection_a,
             psycopg2.extras.Json({'name': 'Test Record A'}), 'main', 1)
        )
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-acc-b-001', collection_b,
             psycopg2.extras.Json({'name': 'Test Record B'}), 'main', 1)
        )
        # Create relation between them (this ensures both collections are captured)
        cur.execute(
            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection_a, 'test-acc-a-001', 'relatedB', collection_b, 'test-acc-b-001', 'main')
        )
        conn.commit()
        print('[OK] Test data created (2 collections with 1 relation)\n')

        # Verify current state - how many collections exist in main branch?
        print('Step 3: Check how many collections exist in main branch')
        cur.execute('SELECT DISTINCT collection FROM dynamic_data WHERE branch_id = %s', ('main',))
        all_branch_collections = [row[0] for row in cur.fetchall()]
        print(f'  Main branch has {len(all_branch_collections)} collections total')
        print(f'  Including: {", ".join(all_branch_collections[:5])}...')
        print()

    # Create version from collection_a (should capture collection_a -> collection_b)
    print('Step 4: Create version from test-empty-a')
    version_info = create_version_snapshot(
        collection=collection_a,
        name='Accuracy Test Version',
        description='Testing that version_collections matches snapshot',
        version_type='branch',
        parent_version=None,
        created_by=test_user,
        branch_id='main'
    )
    version_id = version_info['id']
    print(f'[OK] Version created: {version_id}')
    print(f'  Snapshot contains {version_info["recordsCount"]} records')
    print(f'  Snapshot contains {version_info["relationsCount"]} relations')
    print(f'  Affected collections: {version_info["affectedCollections"]}\n')

    # CRITICAL TEST: Verify version_collections ONLY contains the 2 collections in snapshot
    print('Step 5: Verify version_collections accuracy')
    with get_db() as conn:
        cur = conn.cursor()

        # Check what collections were tracked
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s ORDER BY collection',
            (version_id,)
        )
        tracked_collections = [row[0] for row in cur.fetchall()]

        # Check what collections are in the snapshot
        cur.execute(
            'SELECT DISTINCT collection FROM version_snapshots WHERE version_id = %s ORDER BY collection',
            (version_id,)
        )
        snapshot_collections = [row[0] for row in cur.fetchall()]

        print(f'  Collections in snapshot: {snapshot_collections}')
        print(f'  Collections in version_collections: {tracked_collections}')

        # THE FIX: These should match exactly!
        assert tracked_collections == snapshot_collections, \
            f'CRITICAL BUG: tracked {tracked_collections} but snapshot has {snapshot_collections}'
        print('[PASS] version_collections matches snapshot exactly!\n')

        # Additional check: Should NOT contain unrelated collections from the branch
        unrelated = [c for c in tracked_collections if c not in [collection_a, collection_b]]
        if unrelated:
            print(f'[FAIL] CRITICAL: Tracked unrelated collections: {unrelated}')
            print(f'  This would cause data loss in switch/delete operations!')
            raise AssertionError(f'Tracked {len(unrelated)} unrelated collections')
        print('[PASS] No unrelated collections tracked\n')

        # Summary
        print('=== Test Summary ===')
        print(f'[OK] Version snapshot contains {len(snapshot_collections)} collections')
        print(f'[OK] version_collections tracked {len(tracked_collections)} collections')
        print(f'[OK] Both lists match exactly')
        print(f'[OK] No unrelated collections from branch were tracked')
        print()

    # Cleanup
    print('Step 6: Cleanup test data')
    delete_version(version_id, confirmed=True)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s)', (collection_a, collection_b))
        conn.commit()
    print('[OK] Cleanup complete\n')

    print('=== CRITICAL BUG FIX VERIFIED ===')
    print('The fix ensures:')
    print('1. create_version_snapshot passes explicit list of collections')
    print('2. version_collections tracks ONLY collections actually in snapshot')
    print('3. NO database scan that would include unrelated collections')
    print('4. This prevents data loss/errors in switch/delete operations')

if __name__ == '__main__':
    test_version_collections_accuracy()