"""
Test version snapshot creation with multiple collections
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db
from utils.version import create_version_snapshot, delete_version
import psycopg2.extras

def test_create_version_multi_collection():
    """Test that version snapshot contains all related collections' data"""
    collection_a = 'test-snapshot-a'
    collection_b = 'test-snapshot-b'
    test_user = 'test_multi_snapshot'

    # Setup: Create data in both collections with relations
    with get_db() as conn:
        cur = conn.cursor()

        # Cleanup
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM version_snapshots WHERE version_id LIKE %s', ('ver-test%',))
        cur.execute('DELETE FROM collection_versions WHERE id LIKE %s', ('ver-test%',))
        conn.commit()

        # Insert collection A data
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('snapshot-a-001', collection_a, psycopg2.extras.Json({'name': 'A-1'}), 'main', 1)
        )

        # Insert collection B data
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('snapshot-b-001', collection_b, psycopg2.extras.Json({'name': 'B-1'}), 'main', 1)
        )

        # Create relation: A → B
        cur.execute(
            'INSERT INTO data_relations '
            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection_a, 'snapshot-a-001', 'related', collection_b, 'snapshot-b-001', 'main')
        )
        conn.commit()

    # Test: Create version from collection A
    version_info = create_version_snapshot(
        collection=collection_a,
        name='Multi-Collection Test',
        description='Test',
        version_type='branch',
        parent_version=None,
        created_by=test_user,
        branch_id='main'
    )
    version_id = version_info['id']

    # Verify: Snapshot contains both collections
    with get_db() as conn:
        cur = conn.cursor()

        # Query snapshot collections
        cur.execute(
            'SELECT DISTINCT collection FROM version_snapshots WHERE version_id = %s',
            (version_id,)
        )
        snapshot_collections = [row[0] for row in cur.fetchall()]

        # Both collections should be in snapshot
        assert collection_a in snapshot_collections, f'Collection A should be in snapshot, got: {snapshot_collections}'
        assert collection_b in snapshot_collections, f'Collection B should be in snapshot, got: {snapshot_collections}'

        # Verify record counts
        cur.execute(
            'SELECT COUNT(*) FROM version_snapshots WHERE version_id = %s AND collection = %s',
            (version_id, collection_a)
        )
        count_a = cur.fetchone()[0]
        assert count_a == 1, f'Collection A should have 1 snapshot record, got {count_a}'

        cur.execute(
            'SELECT COUNT(*) FROM version_snapshots WHERE version_id = %s AND collection = %s',
            (version_id, collection_b)
        )
        count_b = cur.fetchone()[0]
        assert count_b == 1, f'Collection B should have 1 snapshot record, got {count_b}'

    # Cleanup
    delete_version(version_id, confirmed=True)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s)', (collection_a, collection_b))
        conn.commit()

    print('[PASS] Multi-collection snapshot test')


if __name__ == '__main__':
    test_create_version_multi_collection()
    print('\nAll snapshot tests passed!')