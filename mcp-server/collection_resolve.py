"""Shared MCP-tool helper: resolve a caller-supplied `collection` identifier
(which may be the canonical collection slug, e.g. "inspection-case", or the
display name of its data-type menu, e.g. "巡检记录") to the real collection
slug. Data-type menu names are globally unique (idx_menus_data_name_unique,
server/init_db.py), so name-based lookup is unambiguous.
"""


def resolve_collection(cur, identifier: str):
    """Return the canonical collection slug for `identifier`, or None if no
    data-type menu matches it either by collection id or by menu name."""
    if not identifier:
        return None
    cur.execute(
        "SELECT page_id FROM menus WHERE menu_type = 'data' "
        "AND (page_id = %s OR page_id = %s OR name = %s) LIMIT 1",
        (identifier, 'page-' + identifier, identifier),
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return None
    page_id = row[0]
    return page_id[5:] if page_id.startswith('page-') else page_id
