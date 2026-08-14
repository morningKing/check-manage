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
LISTFILES_MAX_DEPTH = 8
LISTFILES_MAX_FILES = 1000


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