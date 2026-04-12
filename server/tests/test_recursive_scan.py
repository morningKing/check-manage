"""
Unit tests for recursive data scanning algorithm
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db
from utils.version_scan import scan_all_related_data
import psycopg2.extras

def test_scan_single_collection():
    """Test scanning single collection with no relations"""
    # Setup: Create test collection with data on main branch
    collection = 'test-scan-single'
    with get_db() as conn:
        cur = conn.cursor()

        # Cleanup
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()

        # Insert test data
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) '
            'VALUES (%s, %s, %s, %s)',
            ('test-001', collection, psycopg2.extras.Json({'name': 'Test 1'}), 'main')
        )
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) '
            'VALUES (%s, %s, %s, %s)',
            ('test-002', collection, psycopg2.extras.Json({'name': 'Test 2'}), 'main')
        )
        conn.commit()

    # Test: Scan collection
    result = scan_all_related_data(collection, 'main', max_records=100)

    # Verify
    assert collection in result
    assert len(result[collection]) == 2
    assert result[collection][0]['id'] in ['test-001', 'test-002']

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()

    print('[PASS] Single collection scan test')


def test_scan_cross_collection():
    """Test scanning two collections with relations"""
    collection_a = 'test-scan-a'
    collection_b = 'test-scan-b'

    # Setup: Create data in both collections with relation
    with get_db() as conn:
        cur = conn.cursor()

        # Cleanup
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s)', (collection_a, collection_b))
        conn.commit()

        # Insert collection A data
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) '
            'VALUES (%s, %s, %s, %s)',
            ('scan-a-001', collection_a, psycopg2.extras.Json({'name': 'A-1'}), 'main')
        )

        # Insert collection B data
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id) '
            'VALUES (%s, %s, %s, %s)',
            ('scan-b-001', collection_b, psycopg2.extras.Json({'name': 'B-1'}), 'main')
        )

        # Create relation: A → B
        cur.execute(
            'INSERT INTO data_relations '
            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection_a, 'scan-a-001', 'related', collection_b, 'scan-b-001', 'main')
        )
        conn.commit()

    # Test: Scan from collection A
    result = scan_all_related_data(collection_a, 'main', max_records=100)

    # Verify: Both collections scanned
    assert collection_a in result
    assert collection_b in result
    assert len(result[collection_a]) == 1
    assert len(result[collection_b]) == 1

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s)', (collection_a, collection_b))
        conn.commit()

    print('[PASS] Cross-collection scan test')


def test_scan_size_limit():
    """Test size limit protection"""
    collection = 'test-scan-limit'

    # Setup: Create many records (>limit)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()

        # Insert 50 records (under limit for this test)
        for i in range(50):
            cur.execute(
                'INSERT INTO dynamic_data (id, collection, data, branch_id) '
                'VALUES (%s, %s, %s, %s)',
                (f'limit-{i}', collection, psycopg2.extras.Json({'idx': i}), 'main')
            )
        conn.commit()

    # Test: Scan with small limit
    try:
        result = scan_all_related_data(collection, 'main', max_records=10)
        assert False, 'Should raise ValueError for size limit'
    except ValueError as e:
        assert 'Snapshot size exceeds limit' in str(e)

    # Cleanup
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection = %s', (collection,))
        conn.commit()

    print('[PASS] Size limit test')


if __name__ == '__main__':
    test_scan_single_collection()
    test_scan_cross_collection()
    test_scan_size_limit()
    print('\nAll recursive scan tests passed!')