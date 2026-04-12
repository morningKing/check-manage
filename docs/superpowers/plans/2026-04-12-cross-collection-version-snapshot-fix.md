# Cross-Collection Version Snapshot Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix cross-collection version snapshot architecture to correctly initialize all related collections' branch data when switching versions.

**Architecture:** Add `collection` field to `version_snapshots` table, implement recursive BFS scan with circular detection and size limits, modify snapshot creation and version initialization to handle all collections atomically.

**Tech Stack:** Python, PostgreSQL, psycopg2, Flask

---

## File Structure

**Files to create:**
- `server/migrations/migrate_version_snapshots_add_collection.py` - Migration script for DDL change
- `server/utils/version_scan.py` - Recursive data scanning algorithm
- `server/tests/test_recursive_scan.py` - Unit tests for scanning logic

**Files to modify:**
- `server/init_db.py:760-770` - Add migration step to init_db
- `server/utils/version.py:184-287` - Modify `create_version_snapshot()`
- `server/utils/version.py:1352-1494` - Modify `switch_to_version()`
- `server/tests/test_switch_cross_collection.py:17-84` - Update test to cover initialization

**Files to reference (read-only):**
- `docs/superpowers/specs/2026-04-12-cross-collection-version-snapshot-fix-design.md` - Design spec

---

## Task Decomposition

### Task 1: Database Migration - Add collection field to version_snapshots

**Files:**
- Create: `server/migrations/migrate_version_snapshots_add_collection.py`
- Modify: `server/init_db.py:760-770`

- [ ] **Step 1: Write migration script skeleton**

Create file `server/migrations/migrate_version_snapshots_add_collection.py`:

```python
"""
Migration: Add collection field to version_snapshots and backfill existing data
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

def migrate_version_snapshots_collection():
    """Add collection column and backfill from version metadata"""
    pass

if __name__ == '__main__':
    migrate_version_snapshots_collection()
    print('Migration completed!')
```

- [ ] **Step 2: Write failing test for migration**

Create test in `server/tests/test_migration_snapshots.py`:

```python
def test_version_snapshots_has_collection_field():
    """Test that version_snapshots table has collection field after migration"""
    from db import get_db

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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd server && python -m pytest tests/test_migration_snapshots.py::test_version_snapshots_has_collection_field -v`

Expected: FAIL with "collection column should exist"

- [ ] **Step 4: Implement migration logic**

Update `server/migrations/migrate_version_snapshots_add_collection.py`:

```python
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
```

- [ ] **Step 5: Run migration**

Run: `cd server && python migrations/migrate_version_snapshots_add_collection.py`

Expected: SUCCESS with "Migration completed successfully!"

- [ ] **Step 6: Run test to verify it passes**

Run: `cd server && python -m pytest tests/test_migration_snapshots.py::test_version_snapshots_has_collection_field -v`

Expected: PASS

- [ ] **Step 7: Commit migration**

```bash
git add server/migrations/migrate_version_snapshots_add_collection.py
git add server/tests/test_migration_snapshots.py
git commit -m "feat: add collection field to version_snapshots table with migration"
```

---

### Task 2: Implement recursive data scanning algorithm

**Files:**
- Create: `server/utils/version_scan.py`
- Create: `server/tests/test_recursive_scan.py`

- [ ] **Step 1: Write failing tests for recursive scan**

Create `server/tests/test_recursive_scan.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd server && python -m pytest tests/test_recursive_scan.py -v`

Expected: FAIL (function `scan_all_related_data` not defined)

- [ ] **Step 3: Implement recursive scan algorithm**

Create `server/utils/version_scan.py`:

```python
"""
Recursive data scanning for cross-collection version snapshots
"""
from db import get_db
import psycopg2.extras


def query_collection_all_data(collection, branch_id):
    """
    Query all records from a collection on a specific branch.

    Returns: List[Dict] with {id, data, created_at}
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, data, created_at FROM dynamic_data '
            'WHERE collection = %s AND branch_id = %s',
            (collection, branch_id),
        )
        return [
            {'id': row[0], 'data': row[1], 'created_at': row[2]}
            for row in cur.fetchall()
        ]


def query_record_relations(collection, record_id, branch_id):
    """
    Query outgoing relations from a specific record.

    Returns: List[Dict] with {field_name, related_collection, related_id}
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT field_name, related_collection, related_id FROM data_relations '
            'WHERE collection = %s AND record_id = %s AND branch_id = %s',
            (collection, record_id, branch_id),
        )
        return [
            {'field_name': row[0], 'related_collection': row[1], 'related_id': row[2]}
            for row in cur.fetchall()
        ]


def scan_all_related_data(start_collection, branch_id, max_records=10000):
    """
    Recursively scan all related data across collections using BFS.

    Parameters:
    - start_collection: Collection from which version is created
    - branch_id: Branch ID to scan data from
    - max_records: Maximum total records to snapshot (protection)

    Returns:
    - Dict[collection, List[Dict]]: {collection: [{id, data, created_at}]}

    Raises:
    - ValueError: If total records exceeds max_records
    """
    visited = set()  # Circular detection: (collection, record_id) pairs
    all_data = {}    # Result: {collection: [records]}
    total_count = 0

    # BFS queue: (collection, scan_type)
    queue = [(start_collection, 'collection')]

    while queue and total_count < max_records:
        coll, scan_type = queue.pop(0)

        # Skip if already scanned
        if coll in all_data:
            continue

        # Query collection data
        records = query_collection_all_data(coll, branch_id)
        all_data[coll] = records
        total_count += len(records)

        # Scan outgoing relations from each record
        for record in records:
            record_key = (coll, record['id'])
            if record_key in visited:
                continue
            visited.add(record_key)

            relations = query_record_relations(coll, record['id'], branch_id)

            for rel in relations:
                target_coll = rel['related_collection']

                # Add to queue if not scanned
                if target_coll not in all_data:
                    queue.append((target_coll, 'collection'))

    # Size limit check
    if total_count >= max_records:
        raise ValueError(
            f'Snapshot size exceeds limit: {total_count} records (max {max_records}). '
            f'Consider reducing scope or using single-collection version.'
        )

    return all_data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && python -m pytest tests/test_recursive_scan.py -v`

Expected: All 3 tests PASS

- [ ] **Step 5: Commit recursive scan implementation**

```bash
git add server/utils/version_scan.py
git add server/tests/test_recursive_scan.py
git commit -m "feat: implement recursive data scanning with circular detection and size limits"
```

---

### Task 3: Modify create_version_snapshot to use recursive scan

**Files:**
- Modify: `server/utils/version.py:184-287`
- Test: `server/tests/test_version_snapshot_multi_collection.py`

- [ ] **Step 1: Write failing test for multi-collection snapshot**

Create `server/tests/test_version_snapshot_multi_collection.py`:

```python
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
        assert collection_a in snapshot_collections
        assert collection_b in snapshot_collections

        # Verify record counts
        cur.execute(
            'SELECT COUNT(*) FROM version_snapshots WHERE version_id = %s AND collection = %s',
            (version_id, collection_a)
        )
        count_a = cur.fetchone()[0]
        assert count_a == 1

        cur.execute(
            'SELECT COUNT(*) FROM version_snapshots WHERE version_id = %s AND collection = %s',
            (version_id, collection_b)
        )
        count_b = cur.fetchone()[0]
        assert count_b == 1

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && python -m pytest tests/test_version_snapshot_multi_collection.py -v`

Expected: FAIL (snapshot only contains collection_a, not collection_b)

- [ ] **Step 3: Modify create_version_snapshot to use recursive scan**

Edit `server/utils/version.py` lines 217-273:

```python
# Replace lines 217-230 with:
# 1. Recursively scan all related collections' data
from utils.version_scan import scan_all_related_data

try:
    all_collections_data = scan_all_related_data(
        start_collection=collection,
        branch_id=actual_branch_id,
        max_records=10000
    )
except ValueError as e:
    conn.rollback()
    raise ValueError(f'Failed to create version: {str(e)}')

# Replace lines 247-258 (snapshot insert) with:
# 2. Insert all collections' data to version_snapshots
total_records = 0
for coll, records in all_collections_data.items():
    if records:
        snapshot_values = [
            (
                version_id,
                coll,  # ← NEW: collection field
                record['id'],
                psycopg2.extras.Json(record['data']),
                record['created_at']
            )
            for record in records
        ]
        psycopg2.extras.execute_values(
            cur,
            'INSERT INTO version_snapshots '
            '(version_id, collection, record_id, record_data, created_at) '
            'VALUES %s',
            snapshot_values,
        )
        total_records += len(records)

# Replace lines 260-272 (relations insert) with:
# 3. Query and insert relations from ALL collections
all_relations = []
for coll in all_collections_data.keys():
    cur.execute(
        'SELECT collection, record_id, field_name, related_collection, related_id '
        'FROM data_relations '
        'WHERE collection = %s AND branch_id = %s',
        (coll, actual_branch_id),
    )
    all_relations.extend(cur.fetchall())

if all_relations:
    psycopg2.extras.execute_values(
        cur,
        'INSERT INTO version_relations '
        '(version_id, collection, record_id, field_name, related_collection, related_id) '
        'VALUES %s',
        [(version_id, *rel) for rel in all_relations],
    )

# Replace line 244 (records_count) with:
records_count = total_records
relations_count = len(all_relations)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && python -m pytest tests/test_version_snapshot_multi_collection.py -v`

Expected: PASS

- [ ] **Step 5: Commit snapshot creation fix**

```bash
git add server/utils/version.py
git add server/tests/test_version_snapshot_multi_collection.py
git commit -m "feat: create_version_snapshot now captures all related collections' data"
```

---

### Task 4: Modify switch_to_version to initialize all collections

**Files:**
- Modify: `server/utils/version.py:1352-1494`
- Test: `server/tests/test_switch_version_initialization.py`

- [ ] **Step 1: Write failing test for initialization**

Create `server/tests/test_switch_version_initialization.py`:

```python
"""
Test version initialization with multiple collections
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db
from utils.version import create_version_snapshot, switch_to_version, delete_version
import psycopg2.extras

def test_switch_version_initializes_all_collections():
    """Test that switch_to_version initializes ALL collections' branch data"""
    collection_a = 'test-switch-init-a'
    collection_b = 'test-switch-init-b'
    test_user_id = 'test-user-init'
    test_username = 'test_init'

    # Setup: Create data in both collections
    with get_db() as conn:
        cur = conn.cursor()

        # Cleanup
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s)', (collection_a, collection_b))
        conn.commit()

        # Insert main branch data
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('init-a-001', collection_a, psycopg2.extras.Json({'name': 'A-1'}), 'main', 1)
        )
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('init-b-001', collection_b, psycopg2.extras.Json({'name': 'B-1'}), 'main', 1)
        )
        cur.execute(
            'INSERT INTO data_relations '
            '(collection, record_id, field_name, related_collection, related_id, branch_id) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (collection_a, 'init-a-001', 'related', collection_b, 'init-b-001', 'main')
        )
        conn.commit()

    # Create version
    version_info = create_version_snapshot(
        collection=collection_a,
        name='Init Test',
        description='Test',
        version_type='branch',
        parent_version=None,
        created_by=test_username,
        branch_id='main'
    )
    version_id = version_info['id']

    # Delete branch data to simulate uninitialized state
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE branch_id = %s', (version_id,))
        cur.execute('DELETE FROM data_relations WHERE branch_id = %s', (version_id,))
        conn.commit()

    # Test: Switch to version (should initialize)
    result = switch_to_version(version_id, test_username, test_user_id)

    # Verify: BOTH collections initialized
    with get_db() as conn:
        cur = conn.cursor()

        # Check collection A branch data
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection_a, version_id)
        )
        count_a = cur.fetchone()[0]
        assert count_a == 1, f'Collection A should have 1 branch record, got {count_a}'

        # Check collection B branch data
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
            (collection_b, version_id)
        )
        count_b = cur.fetchone()[0]
        assert count_b == 1, f'Collection B should have 1 branch record, got {count_b}'

    # Cleanup
    delete_version(version_id, confirmed=True)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE collection IN (%s, %s)', (collection_a, collection_b))
        cur.execute('DELETE FROM data_relations WHERE collection IN (%s, %s)', (collection_a, collection_b))
        conn.commit()

    print('[PASS] Multi-collection initialization test')


if __name__ == '__main__':
    test_switch_version_initializes_all_collections()
    print('\nAll initialization tests passed!')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && python -m pytest tests/test_switch_version_initialization.py -v`

Expected: FAIL (collection B has 0 branch records)

- [ ] **Step 3: Modify switch_to_version initialization logic**

Edit `server/utils/version.py` lines 1426-1460:

```python
# Replace lines 1427-1432 (existing_count check) with:
# 2. Check if branch data exists for any collection
cur.execute(
    'SELECT collection, COUNT(*) FROM dynamic_data '
    'WHERE branch_id = %s GROUP BY collection',
    (version_id,)
)
existing_counts = {row[0]: row[1] for row in cur.fetchall()}
initialized = len(existing_counts) == 0

# Replace lines 1434-1460 (initialization) with:
# 3. Initialize branch data from snapshot (if not exists)
snapshot_records = []
if not existing_counts:
    # Read ALL collections' snapshot data
    cur.execute(
        'SELECT collection, record_id, record_data, created_at '
        'FROM version_snapshots WHERE version_id = %s',
        (version_id,)
    )
    snapshot_records = cur.fetchall()

    # Initialize each collection's data
    for coll, rid, data, created_at in snapshot_records:
        flat_data = {k: v for k, v in data.items()} if isinstance(data, dict) else {}

        cur.execute(
            'INSERT INTO dynamic_data '
            '(id, collection, data, branch_id, created_at, version) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (rid, coll, psycopg2.extras.Json(flat_data), version_id, created_at, 1),
        )

    # Initialize relations (existing logic)
    cur.execute(
        'SELECT collection, record_id, field_name, related_collection, related_id '
        'FROM version_relations WHERE version_id = %s',
        (version_id,)
    )
    relations = cur.fetchall()
    _replace_collection_relations(cur, None, version_id, relations)

# Replace line 1491 (total_records) with:
total_records = sum(existing_counts.values()) if existing_counts else len(snapshot_records)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && python -m pytest tests/test_switch_version_initialization.py -v`

Expected: PASS

- [ ] **Step 5: Commit initialization fix**

```bash
git add server/utils/version.py
git add server/tests/test_switch_version_initialization.py
git commit -m "feat: switch_to_version now initializes all collections' branch data"
```

---

### Task 5: Update existing cross-collection switch tests

**Files:**
- Modify: `server/tests/test_switch_cross_collection.py:17-84`

- [ ] **Step 1: Update test to verify data initialization**

Edit `server/tests/test_switch_cross_collection.py` lines 17-84, add verification after line 84:

```python
# After existing test code (around line 84), add:
# 6. Verify both collections have branch data (NEW)
with get_db() as conn:
    cur = conn.cursor()

    # Check collection A branch data
    cur.execute(
        'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
        ('inspection-case', version_id)
    )
    count_a = cur.fetchone()[0]
    assert count_a > 0, 'Collection A should have branch data'

    # Check collection B branch data (CRITICAL VERIFICATION)
    cur.execute(
        'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND branch_id = %s',
        ('inspection-plan', version_id)
    )
    count_b = cur.fetchone()[0]
    assert count_b > 0, 'Collection B should have branch data (this is the bug fix)'
```

- [ ] **Step 2: Run updated test**

Run: `cd server && python -m pytest tests/test_switch_cross_collection.py::test_switch_to_version_cross_collection -v`

Expected: PASS with new verification

- [ ] **Step 3: Commit test update**

```bash
git add server/tests/test_switch_cross_collection.py
git commit -m "test: verify multi-collection data initialization in cross-collection switch test"
```

---

### Task 6: Integration testing and verification

**Files:**
- None (manual testing)

- [ ] **Step 1: Run all backend tests**

Run: `cd server && python -m pytest tests/ -v`

Expected: All tests PASS (except pre-existing Phase 1 test failures)

- [ ] **Step 2: Manual testing - Create version**

Manual steps:
1. Start backend: `cd server && python app.py`
2. Open browser: `http://localhost:5173`
3. Login with admin account
4. Navigate to "巡检用例" page
5. Create test case with relation to "巡检计划"
6. Click "版本管理" → "创建版本"
7. Verify: Version created successfully
8. Expected: Snapshot contains both collections (verify in database)

Database verification query:
```sql
SELECT DISTINCT collection FROM version_snapshots WHERE version_id = '<your_version_id>';
-- Expected: Both 'inspection-case' and 'inspection-plan'
```

- [ ] **Step 3: Manual testing - Switch version**

Manual steps:
1. Switch to created version branch
2. Verify: Success message shows "同时切换了 1 个关联集合"
3. Navigate to "巡检计划" page
4. CRITICAL VERIFICATION: Inspect data - should show VERSION branch data, not main data

Database verification:
```sql
SELECT branch_id FROM dynamic_data WHERE id = '<plan_id>';
-- Expected: branch_id = <version_id> (NOT 'main')
```

- [ ] **Step 4: Create final verification commit**

```bash
git add docs/
git commit -m "test: integration testing completed for cross-collection version snapshot fix"
```

---

## Summary

**Implementation sequence:**
1. Database migration (DDL change)
2. Recursive scan algorithm
3. Snapshot creation enhancement
4. Version initialization enhancement
5. Test updates
6. Integration testing

**Total tasks:** 6
**Estimated time:** 3-4 hours with TDD approach

**Key architectural changes:**
- `version_snapshots` table: Add `collection` field, update PK
- Recursive BFS scan with circular detection + size limits
- Atomic initialization across all collections

**Testing coverage:**
- Unit tests for recursive scan
- Unit tests for snapshot creation
- Unit tests for version initialization
- Integration tests for full workflow
- Manual testing checklist

---

## Self-Review Checklist

- ✅ Spec coverage: All sections from design spec have corresponding tasks
- ✅ No placeholders: All tasks contain complete code and exact file paths
- ✅ Type consistency: Function names match across tasks (scan_all_related_data, create_version_snapshot, switch_to_version)
- ✅ TDD approach: All code tasks follow "test → implement → verify" pattern
- ✅ Commit frequency: Each task ends with a commit
- ✅ File paths: Exact paths provided for all modifications

Plan is ready for execution.