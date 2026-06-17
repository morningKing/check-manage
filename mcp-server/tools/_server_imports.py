"""把 <repo>/server 加进 sys.path，供 MCP 工具复用 server/utils 的纯函数
（export_runner / script_runner / export_references，均 Flask-free）。"""
import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parent.parent.parent / 'server'
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))
