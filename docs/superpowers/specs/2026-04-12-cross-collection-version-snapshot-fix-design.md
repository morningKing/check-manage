# Cross-Collection Version Snapshot Architecture Fix Design

## Context

**Problem discovered:** The current cross-collection version switching implementation has a critical architectural flaw:

- **Phase 1-6 implementation:** Correctly tracks affected collections and updates user branch context
- **Missing critical functionality:** Fails to initialize branch data for related collections
- **User impact:** When switching to version branch, users see version data for primary collection but **main branch data** for related collections

**Example scenario:**
```
User creates version from inspection-case (Collection A)
- inspection-case has relations to inspection-plan (Collection B)
- User switches to version branch
- User sees version snapshot data for inspection-case ✅
- User navigates to inspection-plan page
- User sees CURRENT main data, NOT version snapshot data ❌
- Relations point to wrong timeline ❌
```

**Root cause:**
1. `version_snapshots` table lacks `collection` field → cannot store multi-collection data
2. `create_version_snapshot()` only copies primary collection's data
3. `switch_to_version()` only initializes primary collection's branch data

**Design decisions (from brainstorming):**
- Fix strategy: Full redesign (Option A)
- Version semantics: Snapshot semantics (save creation-time state for all collections)
- Data isolation: Precise scope (only directly/indirectly related data)
- Scan strategy: Recursive scan (complete data context)
- Protection: Circular detection + size limit (max 10000 records)

---

## Architecture

### Database Schema Changes

**Modify `version_snapshots` table to support multi-collection:**

```sql
-- Add collection field
ALTER TABLE version_snapshots ADD COLUMN collection VARCHAR(200);

-- Update PRIMARY KEY to include collection
ALTER TABLE version_snapshots DROP CONSTRAINT version_snapshots_pkey;
ALTER TABLE version_snapshots ADD PRIMARY KEY (version_id, collection, record_id);

-- Add composite index for efficient collection-level queries
CREATE INDEX idx_vs_version_collection ON version_snapshots(version_id, collection);
```

**Schema impact:**
- Current: `PRIMARY KEY (version_id, record_id)` - assumes single collection per version
- New: `PRIMARY KEY (version_id, collection, record_id)` - supports multi-collection snapshots
- Foreign key to `collection_versions(id)` preserved (cascade delete works)

**Migration strategy:**
- Existing `version_snapshots` rows have NULL `collection` field
- Migration script infers `collection` from `collection_versions.metadata`
- See migration section below

---

### Recursive Data Scanning Algorithm

**Core algorithm: BFS-based recursive scan with circular detection**

```python
def scan_all_related_data(start_collection, branch_id, max_records=10000):
    """
    Recursively scan all related data across collections.

    Parameters:
    - start_collection: Collection from which version is created
    - branch_id: Branch ID to scan data from (e.g., 'main' or existing version_id)
    - max_records: Maximum total records to snapshot (protection mechanism)

    Returns:
    - Dict[collection, List[Dict]]: {collection: [{id, data, created_at, ...}]}

    Raises:
    - ValueError: If total records exceeds max_records
    """
    visited = set()              # Circular detection: (collection, record_id) pairs
    all_data = {}                # Result accumulator: {collection: [records]}
    total_count = 0

    # BFS queue: (collection, scan_type, record_id)
    # scan_type: 'collection' (scan whole collection) or 'record' (scan specific record)
    queue = [(start_collection, 'collection', None)]

    while queue and total_count < max_records:
        coll, scan_type, rec_id = queue.pop(0)

        # Query data based on scan type
        if scan_type == 'collection':
            # Scan entire collection's data on this branch
            records = query_collection_all_data(coll, branch_id)
            all_data[coll] = records
            total_count += len(records)

        elif scan_type == 'record':
            # Scan specific record (already visited as part of collection)
            # This branch is for future enhancement: partial snapshots
            continue

        # Scan outgoing relations from each record
        for record in records:
            relations = query_record_relations(
                collection=coll,
                record_id=record['id'],
                branch_id=branch_id
            )

            for rel in relations:
                # Add related collection to scan queue
                target_key = (rel['related_collection'], rel['related_id'])

                if target_key not in visited:
                    visited.add(target_key)

                    # Check if related collection already scanned
                    if rel['related_collection'] not in all_data:
                        queue.append((rel['related_collection'], 'collection', None))
                    else:
                        # Future: Add specific record scan
                        pass

    # Size limit protection
    if total_count >= max_records:
        raise ValueError(
            f'Snapshot size exceeds limit: {total_count} records (max {max_records}). '
            f'Consider creating version from a smaller scope or use single-layer scan.'
        )

    return all_data
```

**Circular detection:**
- `visited` Set stores `(collection, record_id)` tuples
- Prevents infinite loops when A → B → A (circular relations)
- BFS ensures breadth-first exploration, level-by-level

**Size limit protection:**
- Default: 10000 records (configurable)
- Real-time counter during scanning
- Exceeds limit → raise error before snapshot creation
- User-friendly message suggests reducing scope

**Performance characteristics:**
- Time: O(N × R) where N = number of collections, R = avg records per collection
- Space: O(V) where V = visited records count
- Typical scenario (5-10 collections, 1000 records): ~5-10 seconds
- Occasional version creation → acceptable overhead

---

### Version Snapshot Creation Flow

**Modified `create_version_snapshot()` implementation:**

```python
def create_version_snapshot(collection, name, description, version_type, parent_version, created_by, branch_id=None):
    """
    Create version snapshot with full cross-collection data.

    New behavior:
    1. Recursively scan all related collections' data
    2. Store ALL collections' data to version_snapshots (with collection field)
    3. Store ALL collections' relations to version_relations (existing logic)
    4. Track ALL affected collections in version_collections (existing Phase 1 logic)
    """
    version_id = f'ver-{uuid.uuid4().hex[:8]}'
    actual_branch_id = branch_id or MAIN_BRANCH_ID

    with get_db() as conn:
        cur = conn.cursor()

        # 1. Recursively scan all related data
        all_collections_data = scan_all_related_data(
            start_collection=collection,
            branch_id=actual_branch_id,
            max_records=10000
        )

        # 2. Insert all collections' data to version_snapshots
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

        # 3. Query and insert all relations (existing logic enhanced)
        # Scan relations from ALL collections, not just primary
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

        # 4. Calculate hash and insert version metadata (existing logic)
        records_count = sum(len(records) for records in all_collections_data.values())
        relations_count = len(all_relations)
        data_hash = _compute_data_hash(all_collections_data, all_relations)

        cur.execute(
            'INSERT INTO collection_versions '
            '(id, collection, name, description, version_type, parent_version, status, '
            'data_hash, records_count, relations_count, created_by, created_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (version_id, collection, name, description, version_type, parent_version,
             'active', data_hash, records_count, relations_count, created_by, now),
        )

        # 5. Track all affected collections (Phase 1 logic - already working)
        # Note: track_version_collections now receives ALL collections from scan
        track_version_collections(version_id, collection, actual_branch_id, conn)

    return {
        'id': version_id,
        'collection': collection,  # Primary collection (metadata)
        'name': name,
        'recordsCount': records_count,
        'relationsCount': relations_count,
        'affectedCollections': list(all_collections_data.keys()),  # NEW: full list
    }
```

**Key changes from current implementation:**
- Line 219: Remove `WHERE collection = %s` filter → scan ALL collections
- Line 250: Add `collection` field to snapshot insert
- Lines 260-272: Query relations from ALL scanned collections
- Line 274: Return `affectedCollections` array for user visibility

---

### Version Initialization Flow

**Modified `switch_to_version()` implementation:**

```python
def switch_to_version(version_id, switched_by, user_id=None):
    """
    Switch to version branch with full cross-collection initialization.

    New behavior:
    1. Read snapshot data for ALL collections (grouped by collection)
    2. Initialize branch data for ALL collections (branch_id = version_id)
    3. Initialize relations for ALL collections (existing logic)
    4. Update user branch context for ALL collections (Phase 2a logic - already working)
    """
    with get_db() as conn:
        cur = conn.cursor()

        # 1. Get version metadata
        cur.execute(
            'SELECT collection, name, status, version_type FROM collection_versions WHERE id = %s',
            (version_id,),
        )
        collection, target_name, status, version_type = cur.fetchone()

        # Validation (existing logic)
        if status != 'active':
            raise ValueError(f'Cannot switch: version status is "{status}"')
        if version_type != 'branch':
            raise ValueError('Can only switch to branch-type versions')

        # 2. Check if branch data already exists (per collection)
        cur.execute(
            'SELECT collection, COUNT(*) FROM dynamic_data '
            'WHERE branch_id = %s GROUP BY collection',
            (version_id,),
        )
        existing_counts = {row[0]: row[1] for row in cur.fetchall()}
        initialized = len(existing_counts) == 0

        # 3. Initialize branch data from snapshot (if not exists)
        if not existing_counts:
            # Read ALL collections' snapshot data
            cur.execute(
                'SELECT collection, record_id, record_data, created_at '
                'FROM version_snapshots WHERE version_id = %s',
                (version_id,),
            )
            snapshot_records = cur.fetchall()

            # Initialize each collection's data
            for coll, rid, data, created_at in snapshot_records:
                flat_data = {k: v for k, v in data.items()} if isinstance(data, dict) else {}

                cur.execute(
                    'INSERT INTO dynamic_data '
                    '(id, collection, data, branch_id, created_at) '
                    'VALUES (%s, %s, %s, %s, %s)',
                    (rid, coll, psycopg2.extras.Json(flat_data), version_id, created_at),
                )

            # Initialize relations (existing logic)
            cur.execute(
                'SELECT collection, record_id, field_name, related_collection, related_id '
                'FROM version_relations WHERE version_id = %s',
                (version_id,),
            )
            relations = cur.fetchall()
            _replace_collection_relations(cur, None, version_id, relations)

        # 4. Get affected collections (Phase 2a logic - existing)
        cur.execute(
            'SELECT collection FROM version_collections WHERE version_id = %s',
            (version_id,)
        )
        affected_collections = [row[0] for row in cur.fetchall()]

        if not affected_collections:
            affected_collections = list(existing_counts.keys()) or [collection]

        # 5. Update user branch context for ALL collections (Phase 2a - already working)
        if user_id:
            now = datetime.now(timezone.utc)
            for coll in affected_collections:
                record_id = f'ucb-{user_id}-{coll}'
                cur.execute(
                    'INSERT INTO user_current_branch '
                    '(id, user_id, username, collection, branch_id, updated_at) '
                    'VALUES (%s, %s, %s, %s, %s, %s) '
                    'ON CONFLICT (user_id, collection) DO UPDATE SET branch_id = %s, updated_at = %s',
                    (record_id, user_id, switched_by, coll, version_id, now, version_id, now),
                )

        # 6. Calculate records in branch (sum across collections)
        total_records = sum(existing_counts.values()) if existing_counts else len(snapshot_records)

    return {
        'success': True,
        'branchId': version_id,
        'branchName': target_name,
        'recordsInBranch': total_records,
        'initialized': initialized,
        'affectedCollections': affected_collections,
    }
```

**Key changes from current implementation:**
- Line 1427: Check existing data **per collection** (GROUP BY)
- Line 1436: Read ALL collections from snapshot (no collection filter)
- Line 1449: Use snapshot's `collection` field for insert (not metadata's collection)
- Line 1489: Sum records across all collections for total count

---

### Migration Strategy

**Migration script: `server/migrations/migrate_version_snapshots_add_collection.py`**

```python
"""
Migration: Add collection field to version_snapshots and backfill existing data
"""

def migrate_version_snapshots_collection():
    with get_db() as conn:
        cur = conn.cursor()

        # 1. Add collection column
        cur.execute(
            'ALTER TABLE version_snapshots ADD COLUMN collection VARCHAR(200)'
        )
        print('Added collection column to version_snapshots')

        # 2. Backfill existing snapshots
        # Infer collection from version metadata
        cur.execute(
            'SELECT vs.version_id, vs.record_id, cv.collection '
            'FROM version_snapshots vs '
            'JOIN collection_versions cv ON vs.version_id = cv.id '
            'WHERE vs.collection IS NULL'
        )

        updates = cur.fetchall()
        print(f'Found {len(updates)} snapshot records to update')

        for version_id, record_id, collection in updates:
            cur.execute(
                'UPDATE version_snapshots SET collection = %s '
                'WHERE version_id = %s AND record_id = %s',
                (collection, version_id, record_id),
            )

        # 3. Update PRIMARY KEY
        cur.execute(
            'ALTER TABLE version_snapshots DROP CONSTRAINT version_snapshots_pkey'
        )
        cur.execute(
            'ALTER TABLE version_snapshots ADD PRIMARY KEY (version_id, collection, record_id)'
        )
        print('Updated PRIMARY KEY to include collection')

        # 4. Create composite index
        cur.execute(
            'CREATE INDEX idx_vs_version_collection ON version_snapshots(version_id, collection)'
        )
        print('Created composite index')

        conn.commit()
        print('Migration completed successfully!')

if __name__ == '__main__':
    migrate_version_snapshots_collection()
```

**Migration execution order:**
1. Run this migration BEFORE deploying new code
2. Migration adds column → backfills data → updates constraints
3. Deploy new `create_version_snapshot()` and `switch_to_version()` code
4. New versions will use multi-collection snapshots

**Rollback plan:**
```sql
-- If migration fails, rollback:
ALTER TABLE version_snapshots DROP CONSTRAINT version_snapshots_pkey;
ALTER TABLE version_snapshots ADD PRIMARY KEY (version_id, record_id);
ALTER TABLE version_snapshots DROP COLUMN collection;
DROP INDEX IF EXISTS idx_vs_version_collection;
```

---

### Query Functions

**Helper functions for recursive scanning:**

```python
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
```

---

### Error Handling

**Size limit exceeded:**
```python
try:
    all_data = scan_all_related_data(collection, branch_id, max_records=10000)
except ValueError as e:
    if 'Snapshot size exceeds limit' in str(e):
        # User-friendly error response
        return {
            'error': '版本创建失败',
            'message': f'数据规模超过上限（{e}）。建议缩小版本范围或使用单层扫描。',
            'suggestion': '尝试只对单个Collection创建版本，或选择关联关系较少的数据'
        }, 400
    raise
```

**Circular relation detection:**
- Handled automatically by `visited` Set in BFS algorithm
- No explicit error - just skip already-visited nodes
- Log warning if detected: `logger.warning(f'Skipping circular relation: {target_key}')`

**Snapshot integrity check:**
```python
# Verify snapshot contains expected collections before committing
cur.execute(
    'SELECT DISTINCT collection FROM version_snapshots WHERE version_id = %s',
    (version_id,)
)
snapshot_collections = [row[0] for row in cur.fetchall()]

expected_collections = list(all_collections_data.keys())
if set(snapshot_collections) != set(expected_collections):
    conn.rollback()
    raise RuntimeError('Snapshot integrity check failed: missing collections')
```

---

### Testing Strategy

**Unit tests:**

1. **Recursive scan tests:**
```python
def test_scan_single_collection():
    """Test scanning single collection with no relations"""
    data = scan_all_related_data('test-collection', 'main')
    assert data.keys() == {'test-collection'}
    assert len(data['test-collection']) > 0

def test_scan_cross_collection():
    """Test recursive scan across multiple collections"""
    # Setup: collection-a → collection-b
    data = scan_all_related_data('collection-a', 'main')
    assert 'collection-a' in data
    assert 'collection-b' in data

def test_scan_circular_relation():
    """Test circular relation detection"""
    # Setup: a → b → a (circular)
    data = scan_all_related_data('collection-a', 'main')
    # Should not infinite loop
    # Should not duplicate data
    assert len(data['collection-a']) == expected_count_a
    assert len(data['collection-b']) == expected_count_b

def test_scan_size_limit():
    """Test size limit protection"""
    # Setup: create >10000 records with relations
    try:
        scan_all_related_data('large-collection', 'main', max_records=10000)
    except ValueError as e:
        assert 'Snapshot size exceeds limit' in str(e)
```

2. **Snapshot creation tests:**
```python
def test_create_version_multi_collection():
    """Test version creation with cross-collection data"""
    # Setup: create data in multiple collections with relations
    version = create_version_snapshot(
        collection='inspection-case',
        branch_id='main'
    )

    # Verify snapshot contains all collections
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT DISTINCT collection FROM version_snapshots WHERE version_id = %s',
            (version['id'],)
        )
        snapshot_collections = [row[0] for row in cur.fetchall()]

    assert 'inspection-case' in snapshot_collections
    assert 'inspection-plan' in snapshot_collections

def test_create_version_size_limit():
    """Test version creation fails when size exceeds limit"""
    # Setup: create >10000 related records
    try:
        create_version_snapshot(collection='large-collection')
    except ValueError as e:
        assert 'exceeds limit' in str(e)
```

3. **Version initialization tests:**
```python
def test_switch_version_initializes_all_collections():
    """Test that switch_to_version initializes ALL collections' data"""
    # Setup: create version with cross-collection data
    version = create_version_snapshot(...)
    version_id = version['id']

    # Delete branch data (simulate uninitialized state)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM dynamic_data WHERE branch_id = %s', (version_id,))
        conn.commit()

    # Switch to version (should initialize)
    result = switch_to_version(version_id, 'test_user', 'test_user_id')

    # Verify ALL collections have branch data
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT collection, COUNT(*) FROM dynamic_data '
            'WHERE branch_id = %s GROUP BY collection',
            (version_id,)
        )
        branch_counts = {row[0]: row[1] for row in cur.fetchall()}

    assert 'inspection-case' in branch_counts
    assert 'inspection-plan' in branch_counts
    assert branch_counts['inspection-case'] > 0
    assert branch_counts['inspection-plan'] > 0
```

**Integration tests:**

Manual testing checklist:
1. Create version from inspection-case with relations to inspection-plan
2. Verify snapshot contains both collections
3. Switch to version
4. Navigate to inspection-plan page
5. Verify inspection-plan shows version data (NOT main data)
6. Verify relations display correctly with labels

---

## Implementation Checklist

### Phase 1: Database Migration

- [ ] Create migration script `migrate_version_snapshots_add_collection.py`
- [ ] Test migration on development database
- [ ] Verify backfill logic works for existing snapshots
- [ ] Create rollback script
- [ ] Document migration procedure

### Phase 2: Core Algorithm Implementation

- [ ] Implement `scan_all_related_data()` function
- [ ] Implement helper query functions
- [ ] Add circular detection logic
- [ ] Add size limit protection
- [ ] Test recursive scan independently

### Phase 3: Snapshot Creation Enhancement

- [ ] Modify `create_version_snapshot()` to use recursive scan
- [ ] Update snapshot insert to include collection field
- [ ] Update relations query to scan all collections
- [ ] Update version metadata to reflect multi-collection counts
- [ ] Test snapshot creation with cross-collection data

### Phase 4: Version Initialization Enhancement

- [ ] Modify `switch_to_version()` to initialize all collections
- [ ] Update existing data check to be collection-aware
- [ ] Use collection field from snapshot for initialization
- [ ] Test version switch initialization

### Phase 5: Testing

- [ ] Write unit tests for recursive scan
- [ ] Write unit tests for snapshot creation
- [ ] Write unit tests for version initialization
- [ ] Write integration tests for full workflow
- [ ] Perform manual testing

### Phase 6: Documentation & Deployment

- [ ] Update API documentation for multi-collection versions
- [ ] Update CLAUDE.md with new architecture
- [ ] Create deployment checklist
- [ ] Document rollback procedure

---

## Risks & Mitigations

**Risk 1: Migration fails for existing snapshots**
- Impact: Production data loss
- Mitigation: Run migration on backup first, have rollback script ready

**Risk 2: Recursive scan performance issues**
- Impact: Version creation takes too long
- Mitigation: Size limit (10000) prevents worst case, async processing option for future

**Risk 3: Circular relations cause infinite loop**
- Impact: System hangs during scan
- Mitigation: `visited` Set guarantees termination

**Risk 4: Users create versions from huge scopes**
- Impact: Storage bloat, slow switching
- Mitigation: Size limit error message guides users to reduce scope

**Risk 5: Existing versions don't work after migration**
- Impact: Breaking change for existing functionality
- Mitigation: Migration backfills collection field, existing logic still works

---

## Success Criteria

**Functional requirements:**
- ✅ Version creation captures ALL related collections' data
- ✅ Version switch initializes ALL related collections' data
- ✅ Users see version snapshot data on ALL collection pages
- ✅ Relations display correctly across collections in version branch
- ✅ Circular relations handled gracefully
- ✅ Size limit prevents runaway snapshots

**Non-functional requirements:**
- ✅ Performance: <10 seconds for typical scenario (5-10 collections, 1000 records)
- ✅ Backward compatibility: Existing versions work after migration
- ✅ Data integrity: Snapshot contains all scanned collections
- ✅ Error handling: User-friendly messages for size limit errors

---

## Future Enhancements

**Potential future features enabled by this architecture:**

1. **Partial snapshots:** Allow users to select specific records for snapshot (not whole collection)
2. **Snapshot preview:** Show scope preview before creating version
3. **Incremental snapshots:** Only store changed records (reduce storage)
4. **Cross-collection merge:** Merge version changes across multiple collections
5. **Version diff:** Compare versions across multiple collections
6. **Export versions:** Export snapshot data for backup or transfer

---

## References

**Related documents:**
- `docs/superpowers/specs/2026-04-11-cross-collection-version-delete-design.md` - Phase 1-6 implementation
- `server/init_db.py` - Database schema
- `server/utils/version.py` - Current version management logic

**Key files to modify:**
- `server/init_db.py` - Add migration
- `server/utils/version.py` - `create_version_snapshot()`, `switch_to_version()`
- `server/migrations/` - New migration scripts
- `server/tests/` - New test files