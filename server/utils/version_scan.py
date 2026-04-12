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