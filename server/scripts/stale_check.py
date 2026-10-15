"""启动前/排障用：平台进程残留体检（默认只读；--clean 执行清理）。

Run: cd server && python scripts/stale_check.py [--json] [--clean]

背景（2026-10-15 生产排障，两类静默故障的共因都是"旧进程被复用"）：
- proxy 启动**无条件**拉起新后端：旧后端占着 BACKEND_URL 端口时，新后端
  bind 失败静默退出，而 proxy 探测到"旧后端在响应"→ 显示启动成功，实际
  服务旧代码（新路由 404、新配置不生效）。
- OC serve 只在**不可达**时才由 proxy 拉起：旧 serve 被复用时，它的 stdio
  仍在旧落点（旧版 proxy 的 proxy-opencode.log / 手工启动的终端），
  server/oc-serve.log 永远不更新——看起来"oc-serve 没日志"。**归属为
  platform 的 serve 也可能是旧代码拉起的**（占着 4096 不落盘）：本脚本用
  「进程创建时间 vs 日志最近 spawn 记录」识别这种"新不新"的残留。
- 历史强杀留下的孤儿 python multiprocessing 子进程会常驻（parent 已死）。

检查项：
  1. 平台端口占用者识别与分类（后端 / 代理 / MCP / OC serve / 未知）
  2. OC serve 归属（平台托管 vs 外部）+ **新旧**（创建时间对 spawn 落盘记录）
  3. 孤儿 python 多进程子进程（parent_pid 已不存在）
  4. oc-serve.log 存在性与新旧（serve 在跑但日志陈旧 = 复用旧实例的典型）
  5. 多后端实例并存（BACKEND_URL 端口 与 FLASK_PORT 同时有后端在听）

--clean：清理被标记且带 pid 的残留——**孤儿进程 + 需重启的 OC serve**；
不碰后端（可能在服务运行中的代理栈），多后端并存只提示。清理后自动复检。

退出码：0 = 干净；2 = 发现残留（便于启动脚本/CI 断言）。
"""
import argparse
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 与 proxy.py 同口径读 server/.env（override=False：进程环境优先）
try:
    from pathlib import Path

    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / '.env',
                override=False)
except Exception:  # noqa: BLE001 —— 缺 dotenv 时按进程环境继续
    pass

from utils import opencode_ownership  # noqa: E402
from utils.opencode_global import serve_listener_pids  # noqa: E402

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OC_SERVE_LOG = os.path.join(SERVER_DIR, 'oc-serve.log')
LEGACY_OC_LOG = os.path.join(SERVER_DIR, 'proxy-opencode.log')
# serve 创建时间与"最近 spawn 落盘记录"的允许偏差：平台拉起时先写分隔行再
# Popen，正常应差 ≤ 数秒；超此值说明该 serve 不是当前落盘路径拉起的。
FRESH_TOLERANCE_SEC = 120


def _env(key: str, default: str = '') -> str:
    return (os.environ.get(key) or default).strip()


def _port(url: str, default: int) -> int:
    try:
        return urlparse(url).port or default
    except ValueError:
        return default


BACKEND_URL = _env('BACKEND_URL', 'http://127.0.0.1:3001')
OPENCODE_URL = _env('OPENCODE_BASE_URL', 'http://127.0.0.1:4096')
MCP_HEALTH_URL = _env('MCP_HEALTH_URL', 'http://127.0.0.1:3003/health')
PROXY_PORT = int(_env('PROXY_PORT', '8080') or 8080)
FLASK_PORT = int(_env('FLASK_PORT', '3002') or 3002)


def _cmdline(pid: int) -> str:
    """进程命令行；取不到返回 ''。"""
    try:
        if sys.platform == 'win32':
            out = subprocess.run(
                ['wmic', 'process', 'where', f'ProcessId={pid}',
                 'get', 'CommandLine'],
                capture_output=True, timeout=15).stdout.decode('utf-8', 'replace')
            lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
            # 首行是表头 CommandLine；无实例时是 No Instance(s) Available.
            return lines[1] if len(lines) > 1 and lines[1] != 'CommandLine' else ''
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            return f.read().replace(b'\0', b' ').decode('utf-8', 'replace').strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def _pid_alive(pid: int) -> bool:
    if sys.platform == 'win32':
        out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV'],
                             capture_output=True, timeout=15).stdout.decode(
                                 'utf-8', 'replace')
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _classify(cmd: str) -> str:
    c = cmd.lower()
    if 'proxy.py' in c:
        return '代理(proxy)'
    if 'waitress' in c or 'app.py' in c:
        return '后端'
    if 'mcp-server' in c or 'mcp_server' in c:
        return 'MCP'
    if 'opencode' in c:
        return 'OC serve'
    if 'spawn_main' in c:
        return 'python 多进程子进程'
    return '未知'


def _listeners(port: int) -> list[dict]:
    out = []
    for pid in serve_listener_pids(port):
        cmd = _cmdline(pid)
        out.append({'pid': pid, 'port': port, 'cmd': cmd[:220],
                    'kind': _classify(cmd)})
    return out


def _kill_hint(pids: list[int]) -> str:
    if sys.platform == 'win32':
        return ' ; '.join(f'taskkill /F /PID {p}' for p in pids)
    return ' ; '.join(f'kill -9 {p}' for p in pids)


def _proc_created(pid: int) -> float | None:
    """进程创建时间（epoch 秒）；取不到返回 None（POSIX 简化：不判新旧）。"""
    if sys.platform != 'win32':
        return None
    try:
        out = subprocess.run(
            ['wmic', 'process', 'where', f'ProcessId={pid}',
             'get', 'CreationDate'],
            capture_output=True, timeout=15).stdout.decode('utf-8', 'replace')
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        # 形如 20261015173615.123456+480（本地时间）
        if len(lines) < 2 or not lines[1][:14].isdigit():
            return None
        import datetime as _dtm
        return _dtm.datetime.strptime(lines[1][:14], '%Y%m%d%H%M%S').timestamp()
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _newest_spawn_ts(path: str = OC_SERVE_LOG) -> float | None:
    """oc-serve.log 里最近一条 spawn 分隔行的时间（epoch 秒）；无记录 → None。"""
    import datetime as _dtm
    newest = None
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for ln in f:
                if 'oc-serve spawn' not in ln:
                    continue
                m = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', ln)
                if m:
                    newest = _dtm.datetime.strptime(
                        m.group(1), '%Y-%m-%d %H:%M:%S').timestamp()
    except OSError:
        return None
    return newest


def _serve_stale(created: float | None, newest_spawn: float | None) -> bool:
    """serve 是否"非当前落盘路径拉起"：无任何 spawn 记录，或创建时间与最近
    记录偏差超容忍。created 取不到时保守返回 False（不误杀）。"""
    if newest_spawn is None:
        return True
    if created is None:
        return False
    return abs(created - newest_spawn) > FRESH_TOLERANCE_SEC


def _kill(pids: list[int]) -> list[dict]:
    results = []
    for pid in pids:
        try:
            if sys.platform == 'win32':
                r = subprocess.run(['taskkill', '/F', '/PID', str(pid)],
                                   capture_output=True, timeout=15)
            else:
                r = subprocess.run(['kill', '-9', str(pid)],
                                   capture_output=True, timeout=15)
            results.append({'pid': pid, 'ok': r.returncode == 0})
        except (OSError, subprocess.SubprocessError) as e:  # noqa: BLE001
            results.append({'pid': pid, 'ok': False, 'error': str(e)[:120]})
    return results


def _python_pids() -> list[int]:
    if sys.platform == 'win32':
        out = subprocess.run(['wmic', 'process', 'where',
                              "name='python.exe'", 'get', 'ProcessId'],
                             capture_output=True, timeout=15).stdout.decode(
                                 'utf-8', 'replace')
        pids = []
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.isdigit():
                pids.append(int(ln))
        return pids
    pids = []
    for name in os.listdir('/proc'):
        if name.isdigit():
            pids.append(int(name))
    return pids


def collect() -> dict:
    backend_port = _port(BACKEND_URL, 3001)
    oc_port = _port(OPENCODE_URL, 4096)
    mcp_port = _port(MCP_HEALTH_URL, 3003)

    ports = [
        ('后端(proxy/BACKEND_URL)', backend_port),
        ('后端(dev/FLASK_PORT)', FLASK_PORT),
        ('MCP', mcp_port),
        ('OC serve', oc_port),
        ('代理(proxy)', PROXY_PORT),
    ]
    seen_ports: set[int] = set()
    listeners: list[dict] = []
    for name, port in ports:
        if port in seen_ports:
            continue
        seen_ports.add(port)
        for item in _listeners(port):
            item['role'] = name
            # 命令行认不出时按端口归属兜底（如 MCP 的裸 `python main.py`）
            if item['kind'] == '未知':
                if port == mcp_port:
                    item['kind'] = 'MCP'
                elif port == oc_port:
                    item['kind'] = 'OC serve'
                elif port == PROXY_PORT:
                    item['kind'] = '代理(proxy)'
                elif port in (backend_port, FLASK_PORT):
                    item['kind'] = '后端'
            listeners.append(item)

    findings: list[dict] = []

    # 1) OC serve：归属 + 新旧（创建时间对 spawn 落盘记录）双信号
    serve_ls = [it for it in listeners if it['port'] == oc_port]
    oc_mode = opencode_ownership.status(oc_port)['mode'] if serve_ls else 'none'
    serve_created = _proc_created(serve_ls[0]['pid']) if serve_ls else None
    newest_spawn = _newest_spawn_ts()
    serve_stale = bool(serve_ls) and _serve_stale(serve_created, newest_spawn)
    if serve_ls and (oc_mode != 'platform' or serve_stale):
        reasons = []
        if oc_mode != 'platform':
            reasons.append(f'归属={oc_mode}（外部/旧实例）')
        if serve_stale:
            reasons.append('启动无对应 spawn 落盘记录（旧版/手工拉起）')
        findings.append({
            'level': 'warn',
            'title': f"OC serve 需重启（{'；'.join(reasons)}）",
            'detail': 'proxy 只在 serve 不可达时才拉起：该实例会被复用，'
                      'oc-serve.log 与插件版本可能停留在旧状态（"没日志"/'
                      '门禁不命中的典型根因）。',
            'suggest': _kill_hint([it['pid'] for it in serve_ls]),
            'pids': [it['pid'] for it in serve_ls],
        })

    # 2) 孤儿 python 多进程子进程（parent 已死）
    orphans: list[dict] = []
    for pid in _python_pids():
        cmd = _cmdline(pid)
        if 'spawn_main' not in cmd:
            continue
        m = re.search(r'parent_pid=(\d+)', cmd)
        if not m:
            continue
        parent = int(m.group(1))
        if not _pid_alive(parent):
            orphans.append({'pid': pid, 'parent': parent})
    if orphans:
        findings.append({
            'level': 'warn',
            'title': f'孤儿 python 子进程 {len(orphans)} 个（父进程已死）',
            'detail': '历史强杀残留，常驻占内存/句柄；与本轮启动无关，可安全清理。',
            'suggest': _kill_hint([o['pid'] for o in orphans]),
            'pids': [o['pid'] for o in orphans],
        })

    # 3) 多后端并存（BACKEND_URL 端口 与 FLASK_PORT 同时有后端在听）
    be_ports = {it['port'] for it in listeners
                if it['kind'] == '后端'}
    if len(be_ports) >= 2:
        findings.append({
            'level': 'warn',
            'title': f'多个后端实例并存（端口 {sorted(be_ports)}）',
            'detail': 'proxy 模式下应先停手动/旧后端再启动：否则 proxy 的新后端'
                      '可能 bind 失败，探测却命中旧后端（看起来启动成功）。',
            'suggest': _kill_hint([it['pid'] for it in listeners
                                   if it['kind'] == '后端']),
        })

    # 4) oc-serve.log 状态
    log_info: dict = {'path': OC_SERVE_LOG, 'exists': os.path.isfile(OC_SERVE_LOG)}
    if log_info['exists']:
        st = os.stat(OC_SERVE_LOG)
        import time as _t
        log_info['mtime'] = _t.strftime('%Y-%m-%d %H:%M:%S',
                                        _t.localtime(st.st_mtime))
        log_info['size'] = st.st_size
        last_spawn = ''
        try:
            with open(OC_SERVE_LOG, 'r', encoding='utf-8', errors='replace') as f:
                for ln in f:
                    if 'oc-serve spawn' in ln:
                        last_spawn = ln.strip()
        except OSError:
            pass
        log_info['lastSpawn'] = last_spawn
    if serve_ls and not log_info['exists']:
        findings.append({
            'level': 'warn',
            'title': 'serve 在跑但 server/oc-serve.log 不存在',
            'detail': '该 serve 不是平台落盘路径（proxy/重启端点/watchdog）拉起的'
                      '——查旧路径 proxy-opencode.log 或它的启动终端。',
            'suggest': '停掉该 serve 后由 proxy 重新拉起（启动输出应显示 '
                       '"OpenCode started"）',
        })

    return {
        'listeners': listeners,
        'serveOwnership': oc_mode,
        'serveCreated': serve_created,
        'serveStale': serve_stale,
        'newestSpawnTs': newest_spawn,
        'orphans': orphans,
        'ocServeLog': log_info,
        'legacyLogExists': os.path.isfile(LEGACY_OC_LOG),
        'findings': findings,
    }


def _print_human(r: dict) -> None:
    print('== 平台进程残留体检 ==')
    print('\n[端口占用]')
    if not r['listeners']:
        print('  （平台端口无监听——栈未启动）')
    for it in r['listeners']:
        print(f"  :{it['port']:<5} {it['role']:<22} pid={it['pid']:<7} "
              f"{it['kind']:<10} {it['cmd'][:90]}")
    print('\n[OC serve 归属/新旧]')
    import time as _t
    fmt = lambda ts: (_t.strftime('%Y-%m-%d %H:%M:%S', _t.localtime(ts))
                      if ts else '—')
    print(f"  mode={r['serveOwnership']}"
          f"{'（平台托管）' if r['serveOwnership'] == 'platform' else ''}"
          f" · 进程创建 {fmt(r.get('serveCreated'))}"
          f" · 最近落盘 spawn {fmt(r.get('newestSpawnTs'))}"
          f"{' · ⚠ 判定为残留' if r.get('serveStale') else ''}")
    print('\n[oc-serve.log]')
    li = r['ocServeLog']
    if li['exists']:
        print(f"  {li['path']}")
        print(f"  最后写入 {li.get('mtime')}  size={li.get('size')}B")
        if li.get('lastSpawn'):
            print(f"  最近 spawn: {li['lastSpawn']}")
    else:
        print(f"  不存在（{li['path']}）"
              + ('；旧路径 proxy-opencode.log 存在' if r['legacyLogExists'] else ''))
    if r['orphans']:
        print('\n[孤儿进程]')
        for o in r['orphans']:
            print(f"  pid={o['pid']}  parent_pid={o['parent']}（已不存在）")
    print('\n[结论]')
    if not r['findings']:
        print('  ✓ 未发现残留。proxy 启动输出应为：'
              '"Starting backend" + "OpenCode started"（而非 "reachable"）。')
    else:
        for i, f in enumerate(r['findings'], 1):
            print(f"  {i}. [{f['level']}] {f['title']}")
            print(f"     {f['detail']}")
            print(f"     处理: {f['suggest']}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description='平台进程残留体检（默认只读）')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    ap.add_argument('--clean', action='store_true',
                    help='清理被标记且带 pid 的残留（孤儿进程 + 需重启的 '
                         'OC serve；不碰后端），随后自动复检')
    args = ap.parse_args()

    r = collect()
    if args.clean:
        killed_any = False
        for f in r['findings']:
            for pid in f.get('pids') or []:
                res = _kill([pid])[0]
                killed_any = True
                print(f"[clean] {'OK  ' if res['ok'] else 'FAIL'} pid={pid} "
                      f"({f['title'][:40]})"
                      + ('' if res['ok'] else f"  {res.get('error', '')}"))
        if not killed_any:
            print('[clean] 无可清理项（findings 未携带 pid）')
        else:
            print('[clean] 复检中…')
            r = collect()   # 清理后复检，如实报告剩余
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        _print_human(r)
    return 2 if r['findings'] else 0


if __name__ == '__main__':
    sys.exit(main())
