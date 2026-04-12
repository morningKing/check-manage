# Manual Test Results: Multi-Collection Version Switching

## Test Date
2026-04-12

## Test Objective
Verify that `switch_to_version()` correctly initializes branch data for all tracked collections when switching to an uninitialized multi-collection version.

## Test Environment
- Database: PostgreSQL `casemanage`
- Test Version: `ver-f0c4c7bb` (4 tracked collections)

## Test Procedure

### 1. Version Selection
Selected version `ver-f0c4c7bb` which tracks 4 collections:
- daily-record
- inspection-case
- inspection-plan
- special-record

### 2. Database State Verification

#### Tracked Collections (version_collections table)
```
daily-record
inspection-case
inspection-plan
special-record
```

#### Snapshot Data (version_snapshots table)
```
inspection-case: 10,402 records
daily-record: (no data)
inspection-plan: (no data)
special-record: (no data)
```

**CRITICAL FINDING**: The version_snapshots table only contains data for `inspection-case`, missing data for the other 3 collections.

### 3. Simulation of Uninitialized State

Deleted existing branch data to simulate uninitialized version:
```sql
DELETE FROM dynamic_data WHERE branch_id = 'ver-f0c4c7bb';
-- Deleted 2 records

DELETE FROM data_relations WHERE branch_id = 'ver-f0c4c7bb';
-- Deleted 1 record
```

### 4. Version Switch Execution

Executed `switch_to_version('ver-f0c4c7bb', 'test_user', 'test_user_id')`

**Result**:
- Success: `True`
- Initialized: `True`
- Records in branch: 10,402
- Affected collections: `['special-record', 'daily-record', 'inspection-case', 'inspection-plan']`

### 5. Verification of Initialized Data

```sql
SELECT collection, COUNT(*)
FROM dynamic_data
WHERE branch_id = 'ver-f0c4c7bb'
GROUP BY collection;
```

**Result**:
- `inspection-case`: 10,402 records
- `daily-record`: **MISSING**
- `inspection-plan`: **MISSING**
- `special-record`: **MISSING**

## Test Result: FAILED (Expected Failure)

### Failure Analysis

The test **failed as expected** due to incomplete snapshot data:

1. **Root Cause**: The `version_snapshots` table only contains data for `inspection-case`, not for the other 3 tracked collections.

2. **Why This Happened**: Existing versions were created before the multi-collection version support was fully implemented. The snapshot creation logic was not capturing all tracked collections.

3. **Current Behavior**: `switch_to_version()` correctly:
   - Identifies all tracked collections from `version_collections` table
   - Attempts to initialize all collections
   - Only restores data that exists in `version_snapshots`
   - Reports success even though initialization is incomplete

4. **Impact**: Branch data is partially initialized, leaving 3 out of 4 collections empty.

### Code Review: Why No Error Was Raised

The `switch_to_version()` function (lines 1486-1517) reads from `version_snapshots`:

```python
cur.execute(
    'SELECT collection, record_id, record_data, created_at FROM version_snapshots WHERE version_id = %s',
    (version_id,)
)
target_records = cur.fetchall()
```

It then iterates over whatever records are returned:

```python
for coll, rid, data, created_at in target_records:
    # Insert records...
```

**Missing Validation**: The function does NOT verify that all tracked collections have snapshot data. It silently initializes only the collections that have snapshots.

### Expected Behavior vs Actual

| Aspect | Expected | Actual |
|--------|----------|--------|
| Initialize `inspection-case` | ✅ 10,402 records | ✅ 10,402 records |
| Initialize `daily-record` | ✅ X records | ❌ 0 records (no snapshot) |
| Initialize `inspection-plan` | ✅ Y records | ❌ 0 records (no snapshot) |
| Initialize `special-record` | ✅ Z records | ❌ 0 records (no snapshot) |
| Error Handling | Should warn about missing snapshots | Silent partial initialization |

## Recommendations

### 1. Immediate Fix: Add Validation
The `switch_to_version()` function should validate that all tracked collections have snapshot data before initialization:

```python
# After line 1459 in version.py
snapshot_collections = {row[0] for row in target_records}
missing_snapshots = set(affected_collections) - snapshot_collections
if missing_snapshots:
    raise ValueError(
        f'Cannot initialize version: missing snapshots for collections: {missing_snapshots}. '
        f'Version may have been created before multi-collection support was implemented.'
    )
```

### 2. Data Migration
Existing versions with incomplete snapshots should be flagged or fixed:

```sql
-- Find versions with incomplete snapshots
SELECT
    vc.version_id,
    array_agg(DISTINCT vc.collection) as tracked_collections,
    array_agg(DISTINCT vs.collection) as snapshot_collections
FROM version_collections vc
LEFT JOIN version_snapshots vs ON vc.version_id = vs.version_id
GROUP BY vc.version_id
HAVING array_length(array_agg(DISTINCT vc.collection), 1) !=
       array_length(array_agg(DISTINCT vs.collection), 1);
```

### 3. Clean Test Environment
To properly test multi-collection version switching, a fresh test version should be created in a clean test database with:
- Complete snapshot data for all tracked collections
- Proper relation data
- Known expected record counts

### 4. Automated Testing
Add unit tests to `test_branch_isolation.py`:
- Test switching to multi-collection version with complete snapshots
- Test switching with incomplete snapshots (should raise error)
- Test switching to already-initialized version
- Verify all tracked collections have branch data after initialization

## Conclusion

**Manual testing was completed** but revealed a critical limitation:

- ✅ The switching mechanism executes without errors
- ❌ Initialization is incomplete due to missing snapshot data
- ⚠️ Existing versions cannot be used for proper testing
- 📋 Requires either fixing existing data or creating fresh test versions

**Test Status**: FAILED (Expected due to data inconsistency)

**Blocker**: Cannot complete full manual testing without either:
1. Creating a new test version with complete snapshots, OR
2. Fixing existing version snapshot data

The code logic is sound for properly created versions, but needs validation to prevent silent partial initialization.