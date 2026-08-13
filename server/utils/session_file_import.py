"""把会话工作区里「被自动记录过的」产出文件按需导入系统（data_files）。

独立成模块是因为有两个调用方：内部聊天端点（routes/ai_chat.py，登录用户）
与对外批任务 Open API（routes/open_api_batches.py，API key）。导入白名单是
ai_chat_session_files 的记录——没被 git 扫描记录过的路径一律拒绝，防止借
导入端点把工作区里任意文件搬进 data_files。
"""
import os

from utils.workspace import safe_resolve, WorkspacePathError
from utils.workspace_changes import get_recorded_path, set_data_file_id

MAX_IMPORT_PATHS = 100


def import_recorded_files(session_id, workspace_path, paths, uploaded_by=None):
    """把 `paths`（工作区相对路径）逐个导入 data_files。

    幂等：记录上已有 data_file_id 且文件还在的直接返回原 id，不重复入库。
    返回逐路径的结果列表，每项形如：
      {'path', 'status': 'imported'|'existing', 'file': {id,name,size,...}}
      {'path', 'error': ..., 'code': 'NOT_RECORDED'|'BAD_PATH'|'FILE_MISSING'|
                                    'TOO_LARGE'|'IMPORT_FAILED'}
    """
    # 延迟导入：routes.data_files 依赖 Flask 蓝图生态，utils 模块顶层不碰它。
    from routes.data_files import save_workspace_file, data_file_meta

    results = []
    for p in paths:
        p = str(p or '').strip().replace('\\', '/')
        if not p:
            results.append({'path': p, 'error': '路径不能为空', 'code': 'BAD_PATH'})
            continue
        rec = get_recorded_path(session_id, p)
        if rec is None:
            results.append({'path': p, 'error': '该文件不在会话的文件记录中',
                            'code': 'NOT_RECORDED'})
            continue
        if rec['dataFileId']:
            meta = data_file_meta(rec['dataFileId'])
            if meta:
                results.append({'path': p, 'status': 'existing', 'file': meta})
                continue
            # 引用已失效（文件被清过）→ 落到下面重新导入
        try:
            abs_path = safe_resolve(workspace_path, p)
        except WorkspacePathError:
            results.append({'path': p, 'error': '路径无效', 'code': 'BAD_PATH'})
            continue
        if not os.path.isfile(abs_path):
            results.append({'path': p, 'error': '文件在工作区中已不存在',
                            'code': 'FILE_MISSING'})
            continue
        try:
            meta, err = save_workspace_file(abs_path, p, uploaded_by=uploaded_by)
        except Exception:
            meta, err = None, None
        if not meta:
            too_large = bool(err) and err[1] == 413
            results.append({'path': p,
                            'error': '文件过大' if too_large else '导入失败',
                            'code': 'TOO_LARGE' if too_large else 'IMPORT_FAILED'})
            continue
        set_data_file_id(session_id, p, meta['id'])
        results.append({'path': p, 'status': 'imported', 'file': meta})
    return results
