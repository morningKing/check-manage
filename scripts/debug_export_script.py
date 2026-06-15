# coding: utf-8
# 导出脚本本地调试工具
#
# 与沙箱一致的本地执行环境，支持 scope=page / scope=menu。
# 直接从后端 API 拉真实数据运行脚本，错误信息带完整 traceback。
#
# 一致性：当本机可导入 server 代码 + 连到 DB 时，自动复用服务端的安全 builtins
#（_build_safe_globals）与引用解析（resolve_references），与生产 100% 一致；
# 菜单级脚本会像生产一样注入 references（被引用记录、含跨项目）+ 给记录回挂 _relations。
# 若 server 代码/DB 不可达，则降级为本地 builtins 且 references 注入空表（会提示）。
#
# 用法示例：
#
#   # 用本地脚本文件 + 真实数据
#   python scripts/debug_export_script.py --script my_export.py --collection inspection-case --password admin123
#
#   # 调试菜单级脚本（注入 references，可 join 被引用页/跨项目数据）
#   python scripts/debug_export_script.py --script my_menu_export.py --scope menu --collection inspection-case --password admin123
#
#   # 直接调用服务端已保存的脚本（scope 取脚本自身，可用 --scope 覆盖）
#   python scripts/debug_export_script.py --script-id script-fb2294ae --collection inspection-case --password admin123
#
#   # 只看前 10 条（快速调试）
#   python scripts/debug_export_script.py --script my_export.py --collection inspection-case --limit 10 --password admin123
#
#   # 把结果保存到文件
#   python scripts/debug_export_script.py --script my_export.py --collection inspection-case --out /tmp/result.csv --password admin123
#
# 调试技巧：
#   脚本里无法用 print()，但可以把中间变量序列化到 result 查看：
#       result = json.dumps({'fields': fields, 'first': data[:1]}, ensure_ascii=False, indent=2)
#   工具会把 result 完整打印出来。

import argparse
import collections
import csv
import getpass
import io
import json
import math
import re
import sys
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from xml.dom import minidom

try:
    import requests
except ImportError:
    sys.exit("缺少 requests，请先运行: pip install requests")

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import numpy as np
except ImportError:
    np = None


# ---------------------------------------------------------------------------
# 与 server/utils/script_runner.py _build_safe_globals 完全一致
# ---------------------------------------------------------------------------
def _build_sandbox_globals():
    return {
        '__builtins__': {
            'len': len, 'str': str, 'int': int, 'float': float, 'bool': bool,
            'list': list, 'dict': dict, 'tuple': tuple, 'set': set,
            'sorted': sorted, 'reversed': reversed, 'enumerate': enumerate,
            'zip': zip, 'map': map, 'filter': filter, 'range': range,
            'min': min, 'max': max, 'sum': sum, 'abs': abs, 'round': round,
            'isinstance': isinstance, 'hasattr': hasattr,
            '__import__': __import__,
            'None': None, 'True': True, 'False': False,
        },
        'json': json, 're': re, 'math': math, 'collections': collections,
        'datetime': datetime, 'timedelta': timedelta,
        'pd': pd, 'np': np,
        'csv': csv, 'io': io, 'ET': ET, 'minidom': minidom,
    }


# ---------------------------------------------------------------------------
# 尽量复用服务端真实逻辑，保证与生产 100% 一致；不可用（无 server 代码 / 无 DB）则降级。
# ---------------------------------------------------------------------------
_SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
_server_build_globals = None
_resolve_references = None
_db_config = None
_psycopg2 = None
try:
    if str(_SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(_SERVER_DIR))
    from utils.script_runner import _build_safe_globals as _server_build_globals  # type: ignore  # noqa: E402
    from utils.export_references import resolve_references as _resolve_references  # type: ignore  # noqa: E402
    from config import DB_CONFIG as _db_config  # type: ignore  # noqa: E402
    import psycopg2 as _psycopg2  # type: ignore  # noqa: E402
except Exception:
    _server_build_globals = None
    _resolve_references = None
    _db_config = None
    _psycopg2 = None


def build_globals(scope):
    """优先用服务端 _build_safe_globals（builtins/模块与生产逐字一致）；否则降级本地实现。"""
    if _server_build_globals is not None:
        return _server_build_globals('menu' if scope == 'menu' else 'export')
    return _build_sandbox_globals()


def resolve_menu_references(menu_data, branch):
    """连本机 DB 调服务端 resolve_references，给菜单脚本注入 references（与生产 execute_menu_export 一致）。
    无 server 代码 / 无 DB 时返回 {} 并提示——与生产「解析失败不阻断」对齐。"""
    if _resolve_references is None or _db_config is None or _psycopg2 is None:
        print("      [warn] 未能加载服务端引用解析（需可导入 server 代码 + DB 配置），references 注入空表 {}")
        return {}
    try:
        conn = _psycopg2.connect(**_db_config)
        try:
            cur = conn.cursor()
            refs = _resolve_references(cur, menu_data, export_branch=branch)
            conn.commit()
            total = sum(len(v) for v in refs.values())
            print(f"      references 解析：{len(refs)} 个集合 / {total} 条被引用记录")
            return refs
        finally:
            conn.close()
    except Exception as e:
        print(f"      [warn] references 解析失败：{e}，references 注入空表 {{}}")
        return {}


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def login(base_url, username, password):
    resp = requests.post(f"{base_url}/auth/login",
                         json={"username": username, "password": password}, timeout=10)
    resp.raise_for_status()
    token = resp.json().get("token")
    if not token:
        sys.exit("登录失败: " + str(resp.json()))
    return token


def api_get(base_url, path, token, params=None):
    resp = requests.get(f"{base_url}{path}",
                        headers={"Authorization": f"Bearer {token}"},
                        params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_collection(base_url, token, collection, branch, limit):
    """返回 (records, fields, page_name)"""
    params = {"branch_id": branch}
    if limit:
        params.update({"page": 1, "pageSize": limit})
    else:
        params["all"] = "true"
    result = api_get(base_url, f"/{collection}", token, params)
    records = result.get("data", result if isinstance(result, list) else [])

    page_id = f"page-{collection}"
    try:
        cfg = api_get(base_url, f"/pageConfigs/{page_id}", token)
        fields = cfg.get("fields", [])
        page_name = cfg.get("name", collection)
    except Exception:
        fields = []
        page_name = collection
    return records, fields, page_name


def fetch_script(base_url, token, script_id=None, script_name=None):
    """返回 (code, output_format, scope)，支持按 ID 或名称查找"""
    scripts = api_get(base_url, "/exportScripts", token)
    for s in scripts:
        if (script_id and s["id"] == script_id) or \
           (script_name and s["name"] == script_name):
            return s["script"], s.get("outputFormat", "csv"), s.get("scope", "page")
    # 名称模糊提示
    names = [s["name"] for s in scripts]
    key = script_id or script_name
    sys.exit(f"脚本 '{key}' 不存在。可用脚本：{names}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="导出脚本本地调试工具")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--script",           help="本地脚本文件路径")
    src.add_argument("--script-id",        help="服务端脚本 ID（直接从 API 拉）")
    src.add_argument("--script-name",      help="服务端脚本名称（精确匹配）")
    src.add_argument("--list-collections", action="store_true",
                     help="列出所有可用 collection（不执行脚本）")
    src.add_argument("--list-scripts",     action="store_true",
                     help="列出所有可用脚本 ID 和名称（不执行脚本）")
    parser.add_argument("--collection", default=None, help="数据页 collection 名称")
    parser.add_argument("--scope", choices=["page", "row", "menu"], default=None,
                        help="脚本维度；本地 --script 文件默认 page，菜单级脚本请指定 --scope menu（才会注入 references）")
    parser.add_argument("--branch",   default="main",                  help="分支（默认 main）")
    parser.add_argument("--base-url", default="http://localhost:3002", help="后端地址")
    parser.add_argument("--username", default="admin",                 help="用户名")
    parser.add_argument("--password",                                   help="密码（不传则提示输入）")
    parser.add_argument("--limit",    type=int, default=None,          help="只拉前 N 条（调试加速）")
    parser.add_argument("--out",                                        help="结果输出文件（不传则打印到终端）")
    args = parser.parse_args()

    password = args.password or getpass.getpass(f"密码（{args.username}）: ")
    token = login(args.base_url, args.username, password)

    # --list-collections
    if args.list_collections:
        cfgs = api_get(args.base_url, "/pageConfigs", token)
        print(f"{'collection':<35} {'页面名称'}")
        print("-" * 60)
        for c in cfgs:
            coll = c.get("id", "").replace("page-", "")
            print(f"{coll:<35} {c.get('name','')}")
        return

    # --list-scripts
    if args.list_scripts:
        scripts = api_get(args.base_url, "/exportScripts", token)
        print(f"{'ID':<22} {'scope':<8} {'format':<8} 名称")
        print("-" * 60)
        for s in scripts:
            print(f"{s['id']:<22} {s.get('scope','page'):<8} {s.get('outputFormat',''):<8} {s['name']}")
        return

    if not args.collection:
        sys.exit("缺少 --collection，用 --list-collections 查看可用值")

    # 1. 登录
    print(f"[1/4] 登录 {args.base_url}  用户={args.username}", flush=True)
    print("      登录成功")

    # 2. 拉数据
    print(f"[2/4] 拉取 collection={args.collection}  branch={args.branch}", flush=True)
    records, fields, page_name = fetch_collection(
        args.base_url, token, args.collection, args.branch, args.limit)
    print(f"      {len(records)} 条记录  {len(fields)} 个字段")

    # 3. 读脚本
    print("[3/4] 读取脚本", flush=True)
    if args.script:
        # utf-8-sig：容忍编辑器写入的 BOM（否则 exec 会报 U+FEFF SyntaxError）
        script_code = Path(args.script).read_text(encoding="utf-8-sig")
        scope = args.scope or "page"  # 本地文件默认 page；菜单级脚本用 --scope menu
        print(f"      本地文件 {args.script}  ({len(script_code)} 字符)  scope={scope}")
    elif args.script_id:
        script_code, _fmt, saved_scope = fetch_script(args.base_url, token, script_id=args.script_id)
        scope = args.scope or saved_scope
        print(f"      服务端脚本 {args.script_id}  ({len(script_code)} 字符)  scope={scope}")
    else:
        script_code, _fmt, saved_scope = fetch_script(args.base_url, token, script_name=args.script_name)
        scope = args.scope or saved_scope
        print(f"      服务端脚本 '{args.script_name}'  ({len(script_code)} 字符)  scope={scope}")

    # 4. 组装注入变量（与后端 run_export_script / run_menu_export_script 对齐）
    print(f"[4/4] 执行脚本  scope={scope}", flush=True)
    sandbox = build_globals(scope)

    if scope == "menu":
        # menu 级：注入 menu_data / menu_name / total_records / references
        menu_data = [{
            "collection":  args.collection,
            "pageName":    page_name,
            "records":     records,
            "fields":      fields,
            "recordCount": len(records),
        }]
        # 与生产 execute_menu_export 一致：解析引用 + 回挂 _relations + 注入 references
        references = resolve_menu_references(menu_data, args.branch)
        local_ctx = {
            "menu_data":     menu_data,
            "menu_name":     page_name,
            "total_records": len(records),
            "references":    references,
            "result":        None,
            "filename":      None,
            "content_type":  None,
        }
    else:
        # page / row 级：注入 data / fields / page_name
        local_ctx = {
            "data":         records,
            "fields":       fields,
            "page_name":    page_name,
            "result":       None,
            "filename":     None,
            "content_type": None,
        }

    # 单一命名空间执行：globals 与 locals 必须是同一个 dict（与服务端沙箱一致），
    # 否则自定义函数 / 推导式等嵌套作用域看不到注入变量，报 NameError。
    namespace = dict(sandbox)
    namespace.update(local_ctx)
    try:
        exec(script_code, namespace)  # noqa: S102 — 只传一个 dict
    except Exception as exc:
        print(f"\n[ERR] 脚本执行出错: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    result       = namespace.get("result")
    out_filename = namespace.get("filename") or f"{page_name}.dat"
    content_type = namespace.get("content_type") or "application/octet-stream"

    if result is None:
        print("\n[ERR] 脚本未给 result 赋值", file=sys.stderr)
        sys.exit(1)

    # menu scope 返回 list of {filename, content}
    if isinstance(result, list):
        print(f"\n[OK] 执行成功  (menu 模式，{len(result)} 个文件)")
        for i, item in enumerate(result):
            fname = item.get("filename", f"file_{i}")
            content = item.get("content", "")
            content_bytes = content.encode("utf-8") if isinstance(content, str) else content
            print(f"  [{i+1}] {fname}  {len(content_bytes):,} 字节")
            if args.out:
                out_path = Path(args.out).parent / fname
                out_path.write_bytes(content_bytes)
                print(f"       已写入 {out_path}")
            else:
                preview = content_bytes[:2000].decode("utf-8", errors="replace")
                print(f"       --- 预览 ---\n{preview}")
                if len(content_bytes) > 2000:
                    print(f"       ... (共 {len(content_bytes):,} 字节)")
        return

    result_bytes = result.encode("utf-8") if isinstance(result, str) else result
    print(f"\n[OK] 执行成功")
    print(f"   filename     : {out_filename}")
    print(f"   content_type : {content_type}")
    print(f"   size         : {len(result_bytes):,} 字节")

    if args.out:
        Path(args.out).write_bytes(result_bytes)
        print(f"   已写入        : {args.out}")
    else:
        preview = result_bytes[:3000].decode("utf-8", errors="replace")
        print(f"\n--- 输出预览（前 3000 字节）---\n{preview}")
        if len(result_bytes) > 3000:
            print(f"... (共 {len(result_bytes):,} 字节，用 --out 保存完整文件)")


if __name__ == "__main__":
    main()
