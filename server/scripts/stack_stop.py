"""整栈停净：删除平台全套残留进程，给"改完代码重启"一个干净起点。

Run:  cd server && python scripts/stack_stop.py              # 默认 dry-run，只列不杀
      python scripts/stack_stop.py --kill                    # 执行清理（树杀）
      python scripts/stack_stop.py --kill --include-oc       # 连 OC serve 一起停
      python scripts/stack_stop.py --json                    # 机器可读输出

背景（2026-11-02 生产 405 排障）：/api/ai/chat/sessions/running-states 等
新路由 405 的根因是**后端仍在跑旧代码进程**——proxy 被强杀（taskkill /F
不走清理回调）时 backend/MCP/SE 全家变孤儿常驻；新 proxy 启动时旧后端占着
BACKEND_URL 端口，新后端 bind 失败静默退出，探测却命中旧后端 →「启动成功」
实为旧代码服务（新路由 405、新配置不生效）。

与 stale_check.py 的分工：stale_check --clean 刻意不碰后端（可能在服务
运行中的代理栈）；本脚本补上「就是要把整栈停干净」的形态。复用它的全量
进程快照与 .env 读取（wmic CSV/列表格式、\\r\\r\\n 规整等坑都踩平了）。

目标圈定（并集，宁少勿误杀）：
  1. 平台端口监听者——proxy / BACKEND_URL / FLASK_PORT / MCP / SE 端口。
     端口是首要且可信的信号：残留后端一定还占着端口。OC serve 端口仅在
     --include-oc 时纳入（serve 按设计可被外部拥有，手工/服务拉起的不归
     平台管，别顺手杀掉别人的）。
  2. 孤儿 multiprocessing 子进程——spawn_main 且 parent_pid 已不存在，
     历史强杀遗留、无端口可循，无条件清理。

安全线：
  - 永不杀自己：排除自身 pid、命令行含本脚本名的进程、以及自己的祖先链；
  - 默认 dry-run，--kill 才动手；Windows 用 taskkill /F /T 杀树，不留新孤儿；
  - 杀完复检：重扫端口与孤儿，未清零退出码 2（可被启动脚本/CI 断言）。

执行顺序：先杀 proxy，再杀其余——proxy 活着时 SE reconcile 线程会在 10s
内把被杀的 suggest-editor 重拉起来，先杀 proxy 才能停得干净。

不在范围：压测栈端口（:3092 stub / :4097 混沌，测试自管生命周期）、
其他项目的同名进程（如别的 repo 也有 app.py——本脚本按端口圈定，不做
全盘命令行模式扫杀，避免误伤机器上无关服务）。

退出码：0 = 端口已净；2 = 仍有残留（复检未清零）。
"""
import argparse
import json
import os
import subprocess
import sys
import time

try:
    import stale_check as sc          # python scripts/stack_stop.py（scripts 在 sys.path[0]）
except ImportError:                   # python -m scripts.stack_stop（包形态）
    from scripts import stale_check as sc

SELF_MARK = os.path.basename(__file__)

# 杀灭顺序：proxy 最先（防 SE reconcile 10s 内重拉智能写作），其后任意。
_KIND_ORDER = {'代理(proxy)': 0, '后端': 1, 'MCP': 2, '智能写作(SE)': 3,
               'OC serve': 4}
_ORPHAN_ORDER = 5


def _ancestor_pids(procs: dict, start: int) -> set:
    """自己往上数的祖先 pid 集（这些绝不能杀——杀了等于杀自己的宿主）。"""
    out: set = set()
    cur = start
    while True:
        info = procs.get(cur)
        parent = (info or {}).get('parent') or 0
        if not parent or parent in out or parent not in procs:
            return out
        out.add(parent)
        cur = parent


def _exclusion(pid: int, cmd: str, ancestors: set) -> str | None:
    """命中保护线时返回原因（用于打印），否则 None。"""
    if pid == os.getpid():
        return '自身进程'
    if SELF_MARK in (cmd or ''):
        return '命令行含本脚本名'
    if pid in ancestors:
        return '自身的祖先进程'
    return None


def _descendants(procs: dict, root: int) -> list:
    """root 的全部子孙 pid（POSIX 无 taskkill /T，按快照补齐树杀）。"""
    children: dict[int, list] = {}
    for pid, info in procs.items():
        children.setdefault(info.get('parent') or 0, []).append(pid)
    out: list = []
    stack = [root]
    while stack:
        cur = stack.pop()
        for ch in children.get(cur, []):
            if ch != root:
                out.append(ch)
                stack.append(ch)
    return out


def _kill_tree(pid: int, procs: dict) -> tuple[bool, str]:
    """杀单棵树。Windows taskkill /F /T 一步到位；POSIX 逐个 kill -9。"""
    try:
        if sys.platform == 'win32':
            r = subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                               capture_output=True, timeout=20)
            ok = r.returncode == 0
            err = '' if ok else (r.stderr or r.stdout or b'').decode(
                'utf-8', 'replace').strip()[:120]
            return ok, err
        ok_all, errs = True, []
        for p in [pid] + [q for q in _descendants(procs, pid) if q != pid]:
            r = subprocess.run(['kill', '-9', str(p)],
                               capture_output=True, timeout=10)
            if r.returncode != 0:
                ok_all = False
                errs.append(f'pid={p}')
        return ok_all, ';'.join(errs)
    except (OSError, subprocess.SubprocessError) as e:  # noqa: BLE001
        return False, str(e)[:120]


def collect(include_oc: bool) -> dict:
    """圈定目标进程。返回 {targets, ocPort, ocSkipped, snapshotDegraded}。"""
    procs = sc.snapshot_processes()
    backend_port = sc._port(sc.BACKEND_URL, 3001)
    oc_port = sc._port(sc.OPENCODE_URL, 4096)
    mcp_port = sc._port(sc.MCP_HEALTH_URL, 3003)

    wanted = [
        ('代理(proxy)', sc.PROXY_PORT),
        ('后端(BACKEND_URL)', backend_port),
        ('后端(FLASK_PORT)', sc.FLASK_PORT),
        ('MCP', mcp_port),
        ('智能写作(SE)', sc.SE_WS_PORT),
    ]
    if include_oc:
        wanted.append(('OC serve', oc_port))

    ancestors = _ancestor_pids(procs, os.getpid())
    seen_ports: set = set()
    seen_pids: set = set()
    targets: list[dict] = []
    for role, port in wanted:
        if port in seen_ports:
            continue
        seen_ports.add(port)
        for pid in sc.serve_listener_pids(port):
            if pid in seen_pids:
                continue
            cmd = (procs.get(pid) or {}).get('cmd') or sc._cmdline_one(pid)
            why = _exclusion(pid, cmd, ancestors)
            if why:
                targets.append({'pid': pid, 'port': port, 'role': role,
                                'kind': '跳过', 'cmd': cmd[:160],
                                'excluded': why})
                continue
            seen_pids.add(pid)
            kind = sc._classify(cmd)
            if kind == '未知':   # 命令行认不出按端口兜底（MCP 裸 main.py / SE node）
                if port == mcp_port:
                    kind = 'MCP'
                elif port == sc.SE_WS_PORT:
                    kind = '智能写作(SE)'
                elif port == oc_port:
                    kind = 'OC serve'
                elif port == sc.PROXY_PORT:
                    kind = '代理(proxy)'
                elif port in (backend_port, sc.FLASK_PORT):
                    kind = '后端'
            targets.append({'pid': pid, 'port': port, 'role': role,
                            'kind': kind, 'cmd': cmd[:160]})

    # 孤儿 multiprocessing 子进程：父已死、无端口可循，无条件清理。
    for pid, info in procs.items():
        if pid in seen_pids or 'spawn_main' not in info['cmd']:
            continue
        m = sc.re.search(r'parent_pid=(\d+)', info['cmd'])
        if not m or int(m.group(1)) in procs:
            continue
        why = _exclusion(pid, info['cmd'], ancestors)
        targets.append({'pid': pid, 'port': None,
                        'role': '孤儿multiprocessing子进程',
                        'kind': '跳过' if why else '孤儿',
                        'cmd': info['cmd'][:160], 'excluded': why})

    live = [t for t in targets if t['kind'] != '跳过']
    live.sort(key=lambda t: _KIND_ORDER.get(t['kind'], _ORPHAN_ORDER))
    return {'targets': targets, 'ocPort': oc_port,
            'ocSkipped': not include_oc, 'snapshotDegraded': not procs}


def verify(include_oc: bool) -> dict:
    """复检：平台端口是否仍有监听、孤儿是否仍在。"""
    procs = sc.snapshot_processes()
    oc_port = sc._port(sc.OPENCODE_URL, 4096)
    mcp_port = sc._port(sc.MCP_HEALTH_URL, 3003)
    ports = [('代理(proxy)', sc.PROXY_PORT),
             ('后端(BACKEND_URL)', sc._port(sc.BACKEND_URL, 3001)),
             ('后端(FLASK_PORT)', sc.FLASK_PORT),
             ('MCP', mcp_port),
             ('智能写作(SE)', sc.SE_WS_PORT)]
    if include_oc:
        ports.append(('OC serve', oc_port))
    seen: set = set()
    survivors: list[dict] = []
    for role, port in ports:
        if port in seen:
            continue
        seen.add(port)
        for pid in sc.serve_listener_pids(port):
            survivors.append({'pid': pid, 'port': port, 'role': role,
                              'cmd': ((procs.get(pid) or {}).get('cmd')
                                      or sc._cmdline_one(pid))[:120]})
    orphans = [pid for pid, info in procs.items()
               if 'spawn_main' in info['cmd']
               and (m := sc.re.search(r'parent_pid=(\d+)', info['cmd']))
               and int(m.group(1)) not in procs]
    return {'survivors': survivors, 'orphanPids': orphans}


def _print_plan(r: dict, kill: bool) -> None:
    print(f"== 整栈停净（{'--kill 执行' if kill else 'dry-run；加 --kill 才动手'}）==")
    if r['snapshotDegraded']:
        print('[warn] 进程快照不可用（wmic 失败/缺失）：仅端口维度可用')
    print(f"\n[目标进程] {sum(1 for t in r['targets'] if t['kind'] != '跳过')} 个"
          + ('' if r['targets'] else '（平台端口无监听、无孤儿——栈已是干净状态）'))
    for i, t in enumerate(r['targets'], 1):
        line = (f"  #{i} pid={t['pid']:<7}"
                + (f":{t['port']:<5}" if t['port'] else '      ')
                + f"{t['role']:<20} {t['kind']:<8} {t['cmd'][:70]}")
        if t['kind'] == '跳过':
            line += f"  （保护线跳过：{t['excluded']}）"
        print(line)
    if r['ocSkipped']:
        print(f"\n[未纳入] OC serve :{r['ocPort']}——默认不碰（外部拉起的 serve "
              f"可能别有所属）。确认要一起停再加 --include-oc。")
    if any(t['kind'] != '跳过' for t in r['targets']):
        print('\n[顺序] 先杀 proxy（防 SE reconcile 10s 内重拉智能写作），再其余。')


def main() -> int:
    ap = argparse.ArgumentParser(description='整栈停净：删除平台残留进程（默认 dry-run）')
    ap.add_argument('--kill', action='store_true',
                    help='执行清理（默认只列出目标，不动手）')
    ap.add_argument('--include-oc', action='store_true',
                    help='连 OC serve（OPENCODE_BASE_URL 端口）一起停')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    args = ap.parse_args()

    r = collect(args.include_oc)
    result: dict = {'plan': r, 'kills': [], 'verify': None}
    if args.kill:
        procs = sc.snapshot_processes()
        live = [t for t in r['targets'] if t['kind'] != '跳过']
        for t in live:
            ok, err = _kill_tree(t['pid'], procs)
            result['kills'].append({'pid': t['pid'], 'ok': ok, 'error': err})
            if not args.json:
                print(f"[kill] {'OK  ' if ok else 'FAIL'} pid={t['pid']}"
                      f" :{t['port'] or '—'} {t['kind']}"
                      + (f"  {err}" if err else ''))
        if live:
            time.sleep(2.0)   # 等端口释放（LISTEN 消失）
        result['verify'] = verify(args.include_oc)
        if not args.json:
            v = result['verify']
            if not v['survivors'] and not v['orphanPids']:
                print('[复检] ✓ 平台端口已净、无孤儿——可重新启动整栈'
                      '（python proxy.py）。')
            else:
                for s in v['survivors']:
                    print(f"[复检] 残留 pid={s['pid']} :{s['port']} "
                          f"{s['role']}  {s['cmd'][:60]}")
                for pid in v['orphanPids']:
                    print(f"[复检] 残留孤儿 pid={pid}")
                print('[复检] 仍有残留（进程可能刚被拉起或杀失败），可重跑一次。')
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_plan(r, args.kill)
    v = result['verify']
    dirty = bool(v and (v['survivors'] or v['orphanPids']))
    return 2 if dirty else 0


if __name__ == '__main__':
    sys.exit(main())
