"""把 <repo>/server 加进 sys.path，供 MCP 工具复用 server/utils 的纯函数
（export_runner / script_runner / export_references，均 Flask-free）。

用 append（而非 insert(0)）：server/ 与 mcp-server/ 都有顶层 db.py / auth.py，
追加到末尾可保证 MCP 自己的 db/auth/context 始终优先，仅 mcp-server 没有的
模块（utils.*）才回退到 server/，避免 import 顺序导致的模块串台。"""
import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parent.parent.parent / 'server'
if str(_SERVER) not in sys.path:
    sys.path.append(str(_SERVER))
