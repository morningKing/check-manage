"""运行日志跟尾读取（纯函数，无 Flask 依赖；供 routes/ai_logs.py 调用）。

字节偏移增量模型（spec 2026-10-14-log-viewer-design §4）：
- offset=-1 哨兵：从末尾 256KB 窗口回读最后 limit 行，返回 offset=size；
- 增量：从 offset 顺序读（单次 ≤256KB），只吐完整行——最后一行无换行符
  视为半行留在文件里，offset 只推进到最后一个完整行末（不重不漏）；
- offset > size 判定轮转/截断：rotated=True 并按哨兵语义重读末尾窗口；
- 单行超 4000 字符截断显示；整块无换行（超长行）跳读防活锁；
- 文件缺失返回 missing=True（UI 显示等待，不是错误）。

Windows 注意：只读打开，不与写侧（logging RotatingFileHandler / serve
append）互斥；轮转后路径仍是原名，由 size < offset 自动发现。
"""
import os

TAIL_WINDOW_BYTES = 256 * 1024
MAX_READ_BYTES = 256 * 1024
MAX_LINE_CHARS = 4000
TRUNC_SUFFIX = ' …[已截断]'
SKIP_MARKER_TPL = '…[超长行已跳过 {n} 字节]'
DEFAULT_LIMIT = 300
MAX_LIMIT = 500


def _decode_line(raw: bytes) -> str:
    text = raw.decode('utf-8', errors='replace').rstrip('\r')
    if len(text) > MAX_LINE_CHARS:
        text = text[:MAX_LINE_CHARS] + TRUNC_SUFFIX
    return text


def _missing() -> dict:
    return {'offset': 0, 'size': 0, 'rotated': False, 'truncated': False,
            'missing': True, 'lines': []}


def _tail_window(path: str, size: int, limit: int):
    """末尾窗口回读最后 limit 行（跟尾语义：窗口内更早的行舍弃不标记）。"""
    with open(path, 'rb') as f:
        f.seek(max(0, size - TAIL_WINDOW_BYTES))
        raw = f.read()
    end = raw.rfind(b'\n')
    if end < 0:
        return [], size
    lines = [_decode_line(seg) for seg in raw[:end + 1].split(b'\n')[:-1]]
    return lines[-limit:], size


def _read_forward(path: str, offset: int, limit: int):
    """从 offset 顺序读完整行。返回 (lines, consumed_bytes, truncated)。"""
    with open(path, 'rb') as f:
        f.seek(offset)
        raw = f.read(MAX_READ_BYTES)
    lines, pos = [], 0
    while len(lines) < limit:
        nl = raw.find(b'\n', pos)
        if nl < 0:
            break
        lines.append(_decode_line(raw[pos:nl]))
        pos = nl + 1
    if not lines and len(raw) == MAX_READ_BYTES:
        # 整块无换行 = 超长行：跳读防活锁（否则 offset 不推进，永久重读同块）
        return [SKIP_MARKER_TPL.format(n=len(raw))], len(raw), False
    truncated = len(lines) == limit and (
        pos < len(raw) or len(raw) == MAX_READ_BYTES)
    return lines, pos, truncated


def read_tail(path: str, offset: int = -1, limit: int = DEFAULT_LIMIT) -> dict:
    """spec §4.2 响应形状：{offset, size, rotated, truncated, missing, lines}。"""
    if not path or not os.path.isfile(path):
        return _missing()
    size = os.path.getsize(path)
    if offset < 0 or offset > size:
        lines, off = _tail_window(path, size, limit)
        return {'offset': off, 'size': size, 'rotated': offset > size,
                'truncated': False, 'missing': False, 'lines': lines}
    lines, consumed, truncated = _read_forward(path, offset, limit)
    return {'offset': offset + consumed, 'size': size, 'rotated': False,
            'truncated': truncated, 'missing': False, 'lines': lines}
