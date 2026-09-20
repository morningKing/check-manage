"""Tool: download_field_files — query a collection and copy every file attached
on its file/image fields into the session workspace (the agent's current
directory; OpenCode sessions run with directory=workspace_path, so relative
paths from later tool calls resolve there).

Use case: the user asks "把巡检记录里附件都下载下来" — query_collection only
returns the JSONB refs ({uid, name, url}), and read_data_file inlines ONE file
capped at 200KB. This tool dereferences uid → data_files.storage_path → disk
and materializes the actual bytes locally, so the agent can open/analyze them.

Auth: same menu-role gate as query_collection / read_data_file — the caller's
role must appear in the collection's menu roles; admin bypasses; no menu row
means deny for non-admin. It writes only inside the session workspace (never
business data), so read-only roles may use it; the public kefu identity stays
excluded via rbac.KEFU_TOOL_ALLOWLIST.

Naming: files land flat in the destination with their original names; on
collision (with this run's earlier copies OR pre-existing files) a _1/_2
suffix is appended — existing files are never overwritten.
"""

import os
import re
import shutil

import mcp.types as types

from db import get_db
from context import ToolContext
from collection_resolve import resolve_collection
from mongo_query import translate as mongo_translate, remap_labels
from query_engine import _order_clause, _build_label_map

NAME = "download_field_files"

FILE_CONTROL_TYPES = ("file", "image")
DEFAULT_MAX_FILES = 100
MAX_FILES = 500
MAX_RECORDS = 2000
_MAX_STEM = 100  # keep deduped names comfortably under fs name limits


TOOL = types.Tool(
    name=NAME,
    description=(
        "按条件查询某个数据页，把命中记录里文件/图片字段挂载的所有文件下载到"
        "当前目录（会话工作区根目录），供后续直接读取分析。"
        "collection 可传集合标识（如 inspection-case）或数据页显示名称（如「巡检记录」）；"
        "想知道哪些字段是文件字段，用 list_collections。"
        "参数：collection(必填)、filter(MongoDB 风格，同 query_collection，支持中文标签)、"
        "field=只下载指定文件字段(默认该数据页全部 file/image 字段)、"
        "limit=最多下载文件数(默认 100，上限 500)、"
        "dest_dir=会话目录下的相对子目录(默认当前目录根)、sort({字段:1|-1})。"
        "返回：dest=落盘绝对路径、downloaded[]=(record/field/name/path/size)、"
        "skipped[]=未下载原因、truncated=是否因 limit 截断。"
        "重名文件自动加 _1/_2 后缀，不覆盖已有文件。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "collection": {"type": "string", "description": "集合标识或数据页显示名称"},
            "filter": {"type": "object", "description": "MongoDB 风格筛选条件，缺省为全部记录"},
            "field": {"type": "string", "description": "只下载该文件字段（fieldName 或中文标签），缺省为全部文件字段"},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_FILES,
                      "description": f"最多下载文件数，默认 {DEFAULT_MAX_FILES}"},
            "dest_dir": {"type": "string", "description": "落盘的相对子目录（相对于当前目录），如 attachments"},
            "sort": {"type": "object", "description": "记录排序，如 {createdAt: -1}"},
        },
        "required": ["collection"],
        "additionalProperties": False,
    },
)


class DownloadFieldFilesError(Exception):
    pass


def _workspace_for_session(session_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT workspace_path FROM ai_chat_sessions WHERE id = %s AND status = 'active'",
            (session_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _resolve_dest_dir(ws: str, dest_dir: str) -> str:
    """Destination dir inside the session workspace only — the MCP server may
    run on another machine than the caller, and an unscoped absolute path would
    let a session token write anywhere on the server host."""
    ws_real = os.path.realpath(ws)
    if not dest_dir:
        os.makedirs(ws_real, exist_ok=True)
        return ws_real
    if os.path.isabs(dest_dir) or re.match(r"^[A-Za-z]:", dest_dir) or dest_dir.startswith("~"):
        raise DownloadFieldFilesError("dest_dir 必须是当前目录下的相对路径（如 attachments）")
    dest = os.path.realpath(os.path.join(ws_real, dest_dir))
    if os.path.commonpath([dest, ws_real]) != ws_real:
        raise DownloadFieldFilesError("dest_dir 越出当前目录")
    os.makedirs(dest, exist_ok=True)
    return dest


def _allocate_name(dest_root: str, original_name: str, used: set) -> tuple[str, str]:
    """Pick a collision-free name in dest_root: original first, then _1/_2….
    `used` covers names allocated by this run; os.path.exists covers files from
    earlier runs — neither is ever overwritten."""
    base = os.path.basename(original_name.replace("\\", "/")).strip() or "file"
    stem, ext = os.path.splitext(base)
    if len(stem) > _MAX_STEM:
        stem = stem[:_MAX_STEM]
        base = stem + ext
    candidate, i = base, 1
    while True:
        target = os.path.join(dest_root, candidate)
        if candidate.lower() not in used and not os.path.exists(target):
            used.add(candidate.lower())
            return candidate, target
        candidate = f"{stem}_{i}{ext}"
        i += 1


def handle(input: dict, ctx: ToolContext) -> dict:
    inp = input or {}
    collection = (inp.get("collection") or "").strip()
    if not collection:
        raise DownloadFieldFilesError("collection is required")
    filt = inp.get("filter") or {}
    sort = inp.get("sort") or {}
    field_param = (inp.get("field") or "").strip()
    limit = min(max(int(inp.get("limit") or DEFAULT_MAX_FILES), 1), MAX_FILES)

    ws = _workspace_for_session(ctx.session_id)
    if not ws:
        raise DownloadFieldFilesError("session workspace not found")
    ws_real = os.path.realpath(ws)
    # Validate the destination before any DB work so bad input fails fast.
    dest_root = _resolve_dest_dir(ws, (inp.get("dest_dir") or "").strip())

    # Resolution mirrors query_collection: page_configs slug first (covers
    # config-only collections such as AI-scan pages, which have no data menu),
    # then the data-menu display name (globally unique).
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, name, fields FROM page_configs')
        configs = {}
        for pid, pname, pfields in cur.fetchall():
            configs[pid] = {'name': pname, 'fields': pfields or []}
        if ('page-' + collection) not in configs:
            resolved = resolve_collection(cur, collection)
            if not resolved or ('page-' + resolved) not in configs:
                raise DownloadFieldFilesError(f"未找到数据集合：{collection}")
            collection = resolved
        fields = configs['page-' + collection]['fields']

    # Menu-role gate: caller's role must be in the collection's menu roles
    # (admin bypasses; no menu row means deny for non-admin, same as
    # read_data_file — covers config-only collections).
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT roles FROM menus WHERE page_id = %s", ('page-' + collection,))
        mrow = cur.fetchone()
        roles = mrow[0] if mrow else None
        if ctx.role != "admin" and (roles is None or ctx.role not in roles):
            raise DownloadFieldFilesError(f"无权限下载：{collection} 的文件")

    label_map = _build_label_map(fields)

    file_fields = [f.get("fieldName") for f in fields
                   if f.get("controlType") in FILE_CONTROL_TYPES and f.get("fieldName")]
    if field_param:
        fname = label_map.get(field_param, field_param)  # 中文标签 → fieldName
        if fname not in file_fields:
            raise DownloadFieldFilesError(
                f"字段 {field_param} 不是文件/图片字段；可用文件字段：{file_fields or '（无）'}")
        target_fields = [fname]
    else:
        target_fields = file_fields
    if not target_fields:
        raise DownloadFieldFilesError("该数据页没有文件/图片字段")

    q = remap_labels(filt, fields) if filt else {}
    where, params = mongo_translate(q)
    order = _order_clause(sort, label_map)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT COUNT(*) FROM dynamic_data WHERE collection = %s AND (' + where + ')',
            [collection] + params,
        )
        total = cur.fetchone()[0]
        cur.execute(
            'SELECT id, data FROM dynamic_data WHERE collection = %s AND (' + where + ') '
            'ORDER BY ' + order + ' LIMIT %s',
            [collection] + params + [MAX_RECORDS],
        )
        records = [(rid, data or {}) for rid, data in cur.fetchall()]

    # Collect file refs across the target fields, in stable record order.
    refs = []      # (record_id, field, uid, ref_name)
    skipped = []
    for rid, data in records:
        for fname in target_fields:
            val = data.get(fname)
            if not isinstance(val, list):
                continue
            for ref in val:
                if not isinstance(ref, dict):
                    continue
                uid = ref.get("uid") or ref.get("id")
                if not uid:
                    skipped.append({"record": rid, "field": fname,
                                    "name": ref.get("name") or "?",
                                    "reason": "文件元数据缺少 uid/id（可能是早期 mock 数据，需重新上传）"})
                    continue
                refs.append((rid, fname, uid, ref.get("name") or ""))

    truncated = len(refs) > limit
    picked = refs[:limit]

    files_meta = {}
    if picked:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id, original_name, mime_type, size_bytes, storage_path '
                'FROM data_files WHERE id = ANY(%s)',
                ([p[2] for p in picked],),
            )
            for fid, name, mime, size, path in cur.fetchall():
                files_meta[fid] = (name, mime, size, path)

    used: set = set()
    downloaded = []
    total_bytes = 0
    for rid, fname, uid, ref_name in picked:
        meta = files_meta.get(uid)
        if not meta:
            skipped.append({"record": rid, "field": fname, "name": ref_name or uid,
                            "reason": "data_files 表无此文件（可能已被删除）"})
            continue
        name, mime, size, path = meta
        if not path or not os.path.isfile(path):
            skipped.append({"record": rid, "field": fname, "name": name or ref_name,
                            "reason": f"磁盘上文件已不存在: {path}"})
            continue
        _, target = _allocate_name(dest_root, name or ref_name or "file", used)
        shutil.copyfile(path, target)
        total_bytes += size or 0
        # path is reported relative to the workspace root (the caller's cwd).
        rel = os.path.relpath(target, ws_real).replace("\\", "/")
        downloaded.append({"record": rid, "field": fname, "name": name,
                           "path": rel, "size": size, "mime": mime})

    result = {
        "collection": collection,
        "matched": total,
        "scanned": len(records),
        "fields": target_fields,
        "dest": dest_root,
        "downloaded": downloaded,
        "downloaded_count": len(downloaded),
        "total_bytes": total_bytes,
        "skipped": skipped,
        "truncated": truncated,
    }
    if truncated:
        result["hint"] = (f"命中 {len(refs)} 个文件，超过 limit={limit}，仅下载前 {limit} 个；"
                          "可用 filter 缩小范围或调大 limit(上限 500)")
    return result
