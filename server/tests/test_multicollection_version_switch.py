"""
Test: Multi-Collection Version Switching with Complete Snapshots

This test documents the EXPECTED test procedure for switching to a multi-collection
version that has complete snapshot data. This test CANNOT currently run because all
existing versions in the database have incomplete snapshots (created before the
multi-collection implementation was finalized).

To run this test:
1. Create a fresh test version with complete multi-collection snapshots
2. Update TEST_VERSION_ID below with the new version ID
3. Run: python -m pytest tests/test_multicollection_version_switch.py -v
"""

import pytest
import psycopg2
from utils.version import switch_to_version
from config import DB_CONFIG


# This version needs to be created with complete snapshots
TEST_VERSION_ID = 'ver-test-multicollection-complete'


@pytest.fixture
def clean_test_environment():
    """
    Setup: Ensure test version exists with complete snapshots
    Teardown: Clean up test branch data
    """
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Verify test version exists
    cur.execute(
        'SELECT id FROM collection_versions WHERE id = %s',
        (TEST_VERSION_ID,)
    )
    if not cur.fetchone():
        pytest.skip(f'Test version {TEST_VERSION_ID} not found. Create it first with complete snapshots.')

    # Get tracked collections
    cur.execute(
        'SELECT collection FROM version_collections WHERE version_id = %s',
        (TEST_VERSION_ID,)
    )
    tracked_collections = [row[0] for row in cur.fetchall()]

    if len(tracked_collections) < 2:
        pytest.skip(f'Test version must track multiple collections. Found: {tracked_collections}')

    # Verify all collections have snapshot data
    cur.execute(
        'SELECT DISTINCT collection FROM version_snapshots WHERE version_id = %s',
        (TEST_VERSION_ID,)
    )
    snapshot_collections = {row[0] for row in cur.fetchall()}

    missing = set(tracked_collections) - snapshot_collections
    if missing:
        pytest.skip(
            f'Test version has incomplete snapshots. Missing: {missing}. '
            f'Create a complete test version first.'
        )

    # Clean any existing branch data
    cur.execute('DELETE FROM dynamic_data WHERE branch_id = %s', (TEST_VERSION_ID,))
    cur.execute('DELETE FROM data_relations WHERE branch_id = %s', (TEST_VERSION_ID,))
    conn.commit()

    yield {
        'version_id': TEST_VERSION_ID,
        'tracked_collections': tracked_collections,
    }

    # Teardown: Clean test branch data
    cur.execute('DELETE FROM dynamic_data WHERE branch_id = %s', (TEST_VERSION_ID,))
    cur.execute('DELETE FROM data_relations WHERE branch_id = %s', (TEST_VERSION_ID,))
    conn.commit()
    conn.close()


def test_switch_to_multicollection_version_initializes_all_collections(clean_test_environment):
    """
    Test that switching to an uninitialized multi-collection version
    correctly initializes branch data for ALL tracked collections.

    GIVEN: A version tracking multiple collections with complete snapshots
    WHEN: switch_to_version is called with no existing branch data
    THEN: All tracked collections should have initialized branch data
    """
    test_env = clean_test_environment
    version_id = test_env['version_id']
    tracked_collections = test_env['tracked_collections']

    # Execute: Switch to version
    result = switch_to_version(version_id, 'test_user', 'test_user_id')

    # Assert: Switch succeeded
    assert result['success'] is True
    assert result['initialized'] is True
    assert result['affectedCollections'] == tracked_collections

    # Assert: All collections have branch data
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute(
        'SELECT collection, COUNT(*) FROM dynamic_data WHERE branch_id = %s GROUP BY collection',
        (version_id,)
    )
    initialized_collections = {row[0]: row[1] for row in cur.fetchall()}

    for collection in tracked_collections:
        assert collection in initialized_collections, \
            f'Collection {collection} was not initialized'
        assert initialized_collections[collection] > 0, \
            f'Collection {collection} has no records'

    conn.close()


def test_switch_to_already_initialized_version_skips_initialization(clean_test_environment):
    """
    Test that switching to an already-initialized version skips initialization.

    GIVEN: A version with existing branch data
    WHEN: switch_to_version is called again
    THEN: Initialization should be skipped and existing data preserved
    """
    test_env = clean_test_environment
    version_id = test_env['version_id']

    # Execute: First switch (initializes data)
    result1 = switch_to_version(version_id, 'test_user', 'test_user_id')
    assert result1['initialized'] is True

    # Get record counts
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        'SELECT COUNT(*) FROM dynamic_data WHERE branch_id = %s',
        (version_id,)
    )
    count_after_first_switch = cur.fetchone()[0]
    conn.close()

    # Execute: Second switch (should skip initialization)
    result2 = switch_to_version(version_id, 'test_user', 'test_user_id')
    assert result2['initialized'] is False

    # Assert: Record counts unchanged
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        'SELECT COUNT(*) FROM dynamic_data WHERE branch_id = %s',
        (version_id,)
    )
    count_after_second_switch = cur.fetchone()[0]
    conn.close()

    assert count_after_first_switch == count_after_second_switch, \
        'Record count changed after second switch (data should be preserved)'


def test_switch_to_version_with_missing_snapshots_fails():
    """
    Test that switching to a version with incomplete snapshots raises an error.

    GIVEN: A version tracking multiple collections but missing snapshots for some
    WHEN: switch_to_version is called
    THEN: Should raise ValueError with clear error message

    Note: This test uses the existing incomplete version ver-f0c4c7bb
    """
    version_id = 'ver-f0c4c7bb'  # Known incomplete version
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Verify this version has incomplete snapshots
    cur.execute(
        'SELECT collection FROM version_collections WHERE version_id = %s',
        (version_id,)
    )
    tracked = {row[0] for row in cur.fetchall()}

    cur.execute(
        'SELECT DISTINCT collection FROM version_snapshots WHERE version_id = %s',
        (version_id,)
    )
    snapshotted = {row[0] for row in cur.fetchall()}

    missing = tracked - snapshotted
    assert len(missing) > 0, 'Test version should have missing snapshots'

    # Clean branch data to force initialization attempt
    cur.execute('DELETE FROM dynamic_data WHERE branch_id = %s', (version_id,))
    conn.commit()
    conn.close()

    # Execute & Assert: Should raise ValueError
    with pytest.raises(ValueError) as exc_info:
        switch_to_version(version_id, 'test_user', 'test_user_id')

    error_message = str(exc_info.value)
    assert '缺少快照数据' in error_message or 'missing snapshot' in error_message.lower()
    assert any(coll in error_message for coll in missing), \
        f'Error should mention missing collections: {missing}'


if __name__ == '__main__':
    # Run tests with pytest
    pytest.main([__file__, '-v', '-s'])