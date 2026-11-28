"""跨调用方统计会话工作区真实文件：list_session_files + 常量。

从 routes/ai_chat.py::list_files 抽出。两类调用方：
1. owner 端点 `/sessions/<sid>/files`，按 user_id 校验后调本 util；
2. admin 端点 `/ai/chat/admin/batches/<bid>/sessions/<sid>/files`，跨用户调。

不耦合 DB：要补 data_file_id 等会话信息由调用方 `augment_with_data_file_id`
后置 join（见 Task 2）。
"""
import os

# Dirs never scanned for 产出文件: user input, our own .git, well-known noise.
# Hidden dirs (starting with '.') are skipped too. ⚠ outputs/ 故意不在集中——
# 它要被列出（dir='outputs'），与 git_changes 的 .gitignore 名单是两份独立集合。
LISTFILES_SKIP_DIRS = {'uploads', '.git', 'node_modules', '.venv', '__pycache__', '.opencode'}
LISTFILES_MAX_DEPTH = 32
LISTFILES_MAX_FILES = 1000
# @ 联想搜索的评估上限（文件数）：防御超大仓库拖垮请求，触顶即截断返回已评分命中。
SEARCH_EVAL_CAP = 20000
SEARCH_MAX_LIMIT = 50


def list_session_files(workspace_path: str) -> tuple[list[dict], bool]:
    """递归扫描 workspace，返回 (files, truncated)。

    files 每项：{name, path, dir:'uploads'|'outputs'|'workspace', size}
      - 'uploads'：uploads/ 下直子文件
      - 'outputs'：outputs/ 下递归
      - 'workspace'：根或其他非 outputs 子目录
    排除：嵌套 git 仓库、噪声目录、dotfiles、opencode.json。
    truncated 为 True 当达到 LISTFILES_MAX_FILES 上限。
    """
    if not workspace_path:
        return [], False
    out = []
    # uploads/ — user inputs, direct children only
    up = os.path.join(workspace_path, 'uploads')
    if os.path.isdir(up):
        for name in sorted(os.listdir(up)):
            fp = os.path.join(up, name)
            if os.path.isfile(fp):
                out.append({'name': name, 'path': f"uploads/{name}",
                            'dir': 'uploads', 'size': os.path.getsize(fp)})
    ws_real = os.path.realpath(workspace_path)
    base_depth = ws_real.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(workspace_path):
        # A nested git repo's changes belong to 变更文件, not 产出文件 — skip it
        # entirely. The workspace root is OUR auto-init repo, so keep listing it.
        if os.path.realpath(dirpath) != ws_real and os.path.isdir(os.path.join(dirpath, '.git')):
            dirnames[:] = []
            continue
        depth = os.path.realpath(dirpath).rstrip(os.sep).count(os.sep) - base_depth
        if depth >= LISTFILES_MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = sorted(
            d for d in dirnames if d not in LISTFILES_SKIP_DIRS and not d.startswith('.')
        )
        for name in sorted(filenames):
            if name.startswith('.') or name == 'opencode.json':
                continue
            fp = os.path.join(dirpath, name)
            if not os.path.isfile(fp):
                continue
            rel = os.path.relpath(fp, workspace_path).replace(os.sep, '/')
            sub = 'outputs' if rel.startswith('outputs/') else 'workspace'
            out.append({'name': name, 'path': rel, 'dir': sub, 'size': os.path.getsize(fp)})
            if len(out) >= LISTFILES_MAX_FILES:
                return out, True
    return out, False


def augment_with_data_file_id(session_id: str, files: list[dict]) -> None:
    """原地给每个文件项加 dataFileId（从 ai_chat_session_files 表 LEFT JOIN 来）。

    没记录的填 None；live scan 没出现的旧 DB 记录不补（调用方只看 live）。
    单条 SELECT，构造 {path: data_file_id} 后 in-place fill，**不**逐文件 N+1。

    Best-effort：DB 异常时 silent（与 record_session_files 同契约），不阻断 ADMIN 看文件。
    """
    from db import get_db
    if not files:
        return
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT path, data_file_id FROM ai_chat_session_files WHERE session_id = %s",
                (session_id,),
            )
            dfid_map = {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        dfid_map = {}
    for f in files:
        f['dataFileId'] = dfid_map.get(f['path'])


def _mention_score(name_lower: str, path_lower: str, q: str) -> int:
    """@ 联想匹配评分，与前端 src/utils/fileMentions.ts::filterFileItems 镜像：
    空 q 全收（0）；basename 前缀 1 > path 前缀 2 > basename 子串 3 >
    path 子串 4；不匹配 -1（剔除）。"""
    if not q:
        return 0
    if name_lower.startswith(q):
        return 1
    if path_lower.startswith(q):
        return 2
    if q in name_lower:
        return 3
    if q in path_lower:
        return 4
    return -1


def search_session_files(workspace_path: str, query: str,
                         limit: int = 20) -> tuple[list[dict], bool]:
    """@ 联想服务端搜索：扫描 workspace 返回匹配 query 的 top-limit 文件。

    与 list_session_files 同一套排除规则（LISTFILES_SKIP_DIRS、dotfiles、
    opencode.json、深度上限），但**不**整棵跳过嵌套 git 仓库——克隆代码树
    （AGENTS.md 引导 clone 到子目录）正是 @ 联想要触及的主要内容；仓库自身
    的 .git 目录仍在 SKIP_DIRS 里，不会出现在结果中。文件抽屉/产出文件继续走
    list_session_files，行为不变。

    评分见 _mention_score（前端 filterFileItems 镜像），排序 (score, path)，
    截 top limit；truncated=True 表示命中超过 limit 或触及 SEARCH_EVAL_CAP。
    空 q 按扫描序返回前 N 个（uploads 优先），供 @ 裸唤起展示全集。
    """
    if not workspace_path:
        return [], False
    q = (query or '').lstrip('@').strip().lower()
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, SEARCH_MAX_LIMIT))
    hits: list[tuple[int, str, str]] = []   # (score, rel_posix, abs_path)
    evaluated = 0
    eval_capped = False

    def _consider(fp: str, rel: str) -> None:
        nonlocal evaluated, eval_capped
        evaluated += 1
        score = _mention_score(os.path.basename(rel).lower(), rel.lower(), q)
        if score >= 0:
            hits.append((score, rel.replace(os.sep, '/'), fp))
        if evaluated >= SEARCH_EVAL_CAP:
            eval_capped = True

    up = os.path.join(workspace_path, 'uploads')
    if os.path.isdir(up):
        for name in sorted(os.listdir(up)):
            fp = os.path.join(up, name)
            if os.path.isfile(fp):
                _consider(fp, f"uploads/{name}")

    ws_real = os.path.realpath(workspace_path)
    base_depth = ws_real.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(workspace_path):
        # 唯一与 list_session_files 的扫描差异：不做「嵌套 git 仓库整棵跳过」
        # ——.git 目录本身已在 SKIP_DIRS，仓库内容参与搜索
        depth = os.path.realpath(dirpath).rstrip(os.sep).count(os.sep) - base_depth
        if depth >= LISTFILES_MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = sorted(
            d for d in dirnames if d not in LISTFILES_SKIP_DIRS and not d.startswith('.')
        )
        for name in sorted(filenames):
            if name.startswith('.') or name == 'opencode.json':
                continue
            fp = os.path.join(dirpath, name)
            if not os.path.isfile(fp):
                continue
            _consider(fp, os.path.relpath(fp, workspace_path))
            if eval_capped:
                break
        if eval_capped:
            break

    if q:
        # 空 q 保持扫描序（uploads 优先，随后 walk 序）；有 q 才按评分排序
        hits.sort(key=lambda h: (h[0], h[1]))
    truncated = len(hits) > limit or eval_capped
    files = []
    for _score, rel, fp in hits[:limit]:
        sub = 'uploads' if rel.startswith('uploads/') else (
            'outputs' if rel.startswith('outputs/') else 'workspace')
        files.append({'name': os.path.basename(rel), 'path': rel, 'dir': sub,
                      'size': os.path.getsize(fp)})
    return files, truncated