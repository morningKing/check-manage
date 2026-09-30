"""Deterministic server-side export of a collection's data to a real .xlsx file
in a session's outputs/ dir. Used both by the AI chat export-intent fallback and
(mirrored) by the MCP export tool, so users get a real result file, not a script.

Columns follow the page_config field order/labels; complex values are JSON-encoded.
"""

import os
import re
import json
from datetime import datetime

from openpyxl import Workbook
from db import get_db

_EXPORT_INTENT = re.compile(r'(导出|输出|下载|生成).{0,12}(excel|xlsx|表格|数据|报表)', re.I)


def is_export_intent(text: str) -> bool:
    return bool(text and _EXPORT_INTENT.search(text))


def _menu_for_collection(collection: str):
    """Return (page_id, name, roles) for a data-page collection, or None."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT m.page_id, m.name, m.roles FROM menus m "
            "WHERE m.page_id IS NOT NULL AND "
            "(m.page_id = %s OR m.page_id = %s)",
            (collection, 'page-' + collection),
        )
        return cur.fetchone()


def resolve_collection_from_text(text: str):
    """Best-effort: find a data-page whose name appears in `text`.
    Returns (collection, label) or None."""
    if not text:
        return None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT page_id, name FROM menus WHERE page_id IS NOT NULL")
        rows = cur.fetchall()
    # longest name first so '专项巡检' wins over '巡检'
    for page_id, name in sorted(rows, key=lambda r: len(r[1] or ''), reverse=True):
        if name and name in text:
            collection = page_id[5:] if page_id.startswith('page-') else page_id
            return collection, name
    return None


def _columns_for(page_id: str):
    """Return (keys, headers) from the page_config field order/labels."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT fields FROM page_configs WHERE id = %s", (page_id,))
        row = cur.fetchone()
    fields = (row[0] if row else None) or []
    keys, headers = [], []
    for f in fields:
        fn = f.get('fieldName')
        if not fn:
            continue
        keys.append(fn)
        headers.append(f.get('label') or fn)
    return keys, headers


def _cell(value):
    if value is None:
        return ''
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return '是' if value else '否'
    return value


class ExportError(Exception):
    pass


def export_collection_to_xlsx(collection: str, workspace_path: str, role: str = None) -> dict:
    """Export `collection`'s rows to outputs/<collection>-<ts>.xlsx.
    Enforces role visibility (admin sees all). Returns
    {path, rows, columns, label}. Raises ExportError on bad collection / perms.
    """
    menu = _menu_for_collection(collection)
    if not menu:
        raise ExportError(f"未找到数据集合：{collection}")
    page_id, label, roles = menu
    if role and role != 'admin' and role not in (roles or []):
        raise ExportError(f"无权限导出：{label}")

    keys, headers = _columns_for(page_id)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT data FROM dynamic_data WHERE collection = %s "
            "AND (branch_id = 'main' OR branch_id IS NULL) ORDER BY created_at",
            (collection,),
        )
        rows = [r[0] or {} for r in cur.fetchall()]

    # If page_config had no fields, fall back to the union of data keys.
    if not keys:
        seen = []
        for d in rows:
            for k in d.keys():
                if k not in seen:
                    seen.append(k)
        keys, headers = seen, seen

    wb = Workbook()
    ws = wb.active
    ws.title = (label or collection)[:31]
    ws.append(headers or ['(空)'])
    for d in rows:
        ws.append([_cell(d.get(k)) for k in keys])

    out_dir = os.path.join(workspace_path, 'outputs')
    os.makedirs(out_dir, exist_ok=True)
    fname = f"{collection}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
    dest = os.path.join(out_dir, fname)
    wb.save(dest)
    return {'path': f'outputs/{fname}', 'rows': len(rows), 'columns': len(keys), 'label': label}
