"""
Test: switch_to_version() initializes ALL collections' branch data from snapshot

This test verifies that when switching to a version:
1. All collections tracked in version_collections are initialized
2. Each collection's data is read from snapshot's collection field
3. Relations for all collections are restored

Task 4 from: docs/superpowers/plans/2026-04-12-cross-collection-version-snapshot-fix.md
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras
from db import get_db
from utils.version import (
    create_version_snapshot,
    switch_to_version,
    switch_to_main_branch,
)


def test_switch_version_initializes_all_collections():
    """
    Test that switch_to_version initializes branch data for ALL collections
    tracked in version_collections, not just the primary metadata collection.
    """
    with get_db() as conn:
        cur = conn.cursor()

        # Setup: Create two collections with related data
        collection_a = 'test-coll-a-init'
        collection_b = 'test-coll-b-init'
        user_id = 'test-user-init'
        username = 'test-init-user'

        # Clean up any previous test data (thorough cleanup)
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s) OR related_collection IN (%s, %s)',
                   (collection_a, collection_b, collection_a, collection_b))
        # 先删除 version 相关表，避免 FK 约束错误
        cur.execute(
            'DELETE FROM version_snapshots WHERE version_id IN '
            '(SELECT id FROM collection_versions WHERE collection IN (%s, %s))',
            (collection_a, collection_b)
        )
        cur.execute(
            'DELETE FROM version_relations WHERE version_id IN '
            '(SELECT id FROM collection_versions WHERE collection IN (%s, %s))',
            (collection_a, collection_b)
        )
        cur.execute(
            'DELETE FROM version_collections WHERE version_id IN '
            '(SELECT id FROM collection_versions WHERE collection IN (%s, %s))',
            (collection_a, collection_b)
        )
        cur.execute('DELETE FROM user_current_branch WHERE user_id = %s', (user_id,))
        cur.execute('DELETE FROM collection_versions WHERE collection IN (%s, %s)', (collection_a, collection_b))
        conn.commit()

        # Create data in collection A
        record_a1_id = f'{collection_a}-rec-1'
        record_a2_id = f'{collection_a}-rec-2'
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
            (record_a1_id, collection_a, psycopg2.extras.Json({'name': 'Record A1', 'value': 100}), 'main'),
        )
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
            (record_a2_id, collection_a, psycopg2.extras.Json({'name': 'Record A2', 'value': 200}), 'main'),
        )

        # Create data in collection B
        record_b1_id = f'{collection_b}-rec-1'
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) VALUES (%s, %s, %s, %s)',
            (record_b1_id, collection_b, psycopg2.extras.Json({'name': 'Record B1', 'code': 'B1'}), 'main'),
        )

        # Create bidirectional relation: A1 <-> B1 (M:N relation)
        # Forward: A -> B
        cur.execute(
            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection_a, record_a1_id, 'related_b', collection_b, record_b1_id, 'main'),
        )
        # Reverse: B -> A
        cur.execute(
            'INSERT INTO data_relations (collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection_b, record_b1_id, 'related_a', collection_a, record_a1_id, 'main'),
        )

        conn.commit()

        # Step 1: Create a branch version
        version_result = create_version_snapshot(
            collection=collection_a,
            name='Test Multi-Collection Version',
            version_type='branch',
            created_by=username,
            description='Version with cross-collection relations',
            parent_version=None,
        )
        version_id = version_result['id']
        assert version_id, 'Version creation failed'

        # Verify version_collections tracked both collections
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s',
            (version_id,),
        )
        tracked_collections = sorted([row[0] for row in cur.fetchall()])
        assert tracked_collections == [collection_a, collection_b], \
            f'Expected both collections tracked, got: {tracked_collections}'

        # Step 2: Verify snapshot has data for both collections
        cur.execute(
            'SELECT collection, COUNT(*) FROM version_snapshots WHERE version_id = %s GROUP BY collection',
            (version_id,),
        )
        snapshot_counts = {row[0]: row[1] for row in cur.fetchall()}
        assert snapshot_counts.get(collection_a) == 2, f'Expected 2 records in {collection_a}, got {snapshot_counts}'
        assert snapshot_counts.get(collection_b) == 1, f'Expected 1 record in {collection_b}, got {snapshot_counts}'

        # Step 3: Delete branch data to simulate uninitialized state
        cur.execute(
            'DELETE FROM dynamic_data WHERE branch_id = %s',
            (version_id,),
        )
        cur.execute(
            'DELETE FROM data_relations WHERE collection IN (%s, %s) AND branch_id = %s',
            (collection_a, collection_b, version_id),
        )
        conn.commit()

        # Verify deletion
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE branch_id = %s',
            (version_id,),
        )
        assert cur.fetchone()[0] == 0, 'Branch data should be empty before switch'

        # Step 4: Switch to the version (should initialize ALL collections)
        switch_result = switch_to_version(
            version_id=version_id,
            switched_by=username,
            user_id=user_id,
        )
        assert switch_result['success'], 'Switch failed'
        assert switch_result['initialized'] is True, 'Should have initialized branch data'
        assert collection_a in switch_result['affectedCollections'], 'Collection A should be in affected'
        assert collection_b in switch_result['affectedCollections'], 'Collection B should be in affected'

        # Step 5: CRITICAL VERIFICATION - Both collections should have branch data
        cur.execute(
            'SELECT collection, COUNT(*) FROM dynamic_data WHERE branch_id = %s GROUP BY collection',
            (version_id,),
        )
        branch_counts = {row[0]: row[1] for row in cur.fetchall()}

        # Debug output
        print(f'Branch counts after switch: {branch_counts}')
        print(f'Expected: {collection_a}=2, {collection_b}=1')

        assert branch_counts.get(collection_a) == 2, \
            f'Expected 2 records in {collection_a} with branch_id={version_id}, got {branch_counts}'
        assert branch_counts.get(collection_b) == 1, \
            f'Expected 1 record in {collection_b} with branch_id={version_id}, got {branch_counts}'

        # Step 6: Verify relations were restored
        # Check forward relation (A -> B)
        cur.execute(
            'SELECT COUNT(*) FROM data_relations '
            'WHERE collection = %s AND record_id = %s AND field_name = %s AND branch_id = %s',
            (collection_a, record_a1_id, 'related_b', version_id),
        )
        relation_count = cur.fetchone()[0]
        assert relation_count > 0, 'Forward relation (A -> B) should be restored'

        # Check reverse relation (B -> A)
        cur.execute(
            'SELECT COUNT(*) FROM data_relations '
            'WHERE collection = %s AND record_id = %s AND field_name = %s AND branch_id = %s',
            (collection_b, record_b1_id, 'related_a', version_id),
        )
        relation_count = cur.fetchone()[0]
        assert relation_count > 0, 'Reverse relation (B -> A) should be restored'

        # Step 7: Verify user current branch was set for both collections
        cur.execute(
            'SELECT collection, branch_id FROM user_current_branch WHERE user_id = %s',
            (user_id,),
        )
        user_branches = {row[0]: row[1] for row in cur.fetchall()}
        assert user_branches.get(collection_a) == version_id, \
            f'User current branch for {collection_a} should be {version_id}, got {user_branches}'
        assert user_branches.get(collection_b) == version_id, \
            f'User current branch for {collection_b} should be {version_id}, got {user_branches}'

        # Cleanup
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM user_current_branch WHERE user_id = %s', (user_id,))
        cur.execute('DELETE FROM version_snapshots WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM version_relations WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
        cur.execute('DELETE FROM collection_versions WHERE id = %s', (version_id,))
        conn.commit()

        print('Test passed: Both collections initialized correctly')


if __name__ == '__main__':
    test_switch_version_initializes_all_collections()