# coding: utf-8
# 导出脚本本地调试工具
#
# 与沙箱 100% 一致的本地执行环境，支持 scope=page / scope=menu。
# 直接从后端 API 拉真实数据运行脚本，错误信息带完整 traceback。
#
# 用法示例：
#
#   # 用本地脚本文件 + 真实数据
#   python scripts/debug_export_script.py --script my_export.py --collection inspection-case --password admin123
#
#   # 直接调用服务端已保存的脚本
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
            'isinstance': isinstance, 'hasattr': hasattr, 'any': any, 'all': all,
            '__import__': __import__,
            'None': None, 'True': True, 'False': False,
        },
        'json': json, 're': re, 'math': math, 'collections': collections,
        'datetime': datetime, 'timedelta': timedelta,
        'pd': pd, 'np': np,
        'csv': csv, 'io': io, 'ET': ET, 'minidom': minidom,
    }


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
    src.add_argument("--script",      help="本地脚本文件路径")
    src.add_argument("--script-id",   help="服务端脚本 ID（直接从 API 拉）")
    src.add_argument("--script-name", help="服务端脚本名称（精确匹配）")
    parser.add_argument("--collection", required=True, help="数据页 collection 名称")
    parser.add_argument("--branch",   default="main",                  help="分支（默认 main）")
    parser.add_argument("--base-url", default="http://localhost:3002", help="后端地址")
    parser.add_argument("--username", default="admin",                 help="用户名")
    parser.add_argument("--password",                                   help="密码（不传则提示输入）")
    parser.add_argument("--limit",    type=int, default=None,          help="只拉前 N 条（调试加速）")
    parser.add_argument("--out",                                        help="结果输出文件（不传则打印到终端）")
    args = parser.parse_args()

    password = args.password or getpass.getpass(f"密码（{args.username}）: ")

    # 1. 登录
    print(f"[1/4] 登录 {args.base_url}  用户={args.username}", flush=True)
    token = login(args.base_url, args.username, password)
    print("      登录成功")

    # 2. 拉数据
    print(f"[2/4] 拉取 collection={args.collection}  branch={args.branch}", flush=True)
    records, fields, page_name = fetch_collection(
        args.base_url, token, args.collection, args.branch, args.limit)
    print(f"      {len(records)} 条记录  {len(fields)} 个字段")

    # 3. 读脚本
    print("[3/4] 读取脚本", flush=True)
    if args.script:
        script_code = Path(args.script).read_text(encoding="utf-8")
        scope = "page"  # 本地文件默认 page；如需 menu 请加 --scope 参数
        print(f"      本地文件 {args.script}  ({len(script_code)} 字符)  scope={scope}")
    elif args.script_id:
        script_code, _fmt, scope = fetch_script(args.base_url, token, script_id=args.script_id)
        print(f"      服务端脚本 {args.script_id}  ({len(script_code)} 字符)  scope={scope}")
    else:
        script_code, _fmt, scope = fetch_script(args.base_url, token, script_name=args.script_name)
        print(f"      服务端脚本 '{args.script_name}'  ({len(script_code)} 字符)  scope={scope}")

    # 4. 组装注入变量（与后端 run_export_script / run_menu_export_script 对齐）
    print(f"[4/4] 执行脚本  scope={scope}", flush=True)
    sandbox = _build_sandbox_globals()

    if scope == "menu":
        # menu 级：注入 menu_data / menu_name / total_records
        menu_data = [{
            "collection":  args.collection,
            "pageName":    page_name,
            "records":     records,
            "fields":      fields,
            "recordCount": len(records),
        }]
        local_ctx = {
            "menu_data":     menu_data,
            "menu_name":     page_name,
            "total_records": len(records),
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

    try:
        exec(script_code, sandbox, local_ctx)  # noqa: S102
    except Exception as exc:
        print(f"\n[ERR] 脚本执行出错: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    result       = local_ctx.get("result")
    out_filename = local_ctx.get("filename") or f"{page_name}.dat"
    content_type = local_ctx.get("content_type") or "application/octet-stream"

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
