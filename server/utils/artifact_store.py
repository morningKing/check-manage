"""Artifact Store（ai-harness-p2 spec §5.5/§9）。

内容寻址产物：sha256 分片存储（storage_key 相对 AI_WORKSPACE_ROOT 的
artifacts/ 前缀），同内容同名去重；产物有稳定 id/引用，workspace 可回收
而产物不丢。下载/预览一律按 artifactId 鉴权（owner/admin）。
"""
import hashlib
import logging
import mimetypes
import os
import secrets
import shutil

logger = logging.getLogger(__name__)


def artifact_root() -> str:
    """内容寻址存储根（AI_WORKSPACE_ROOT/artifacts/，随备份打包策略更新）。"""
    from config import AI_WORKSPACE_ROOT
    return os.path.join(AI_WORKSPACE_ROOT, 'artifacts')


def _storage_key(sha: str, name: str) -> str:
    return f'artifacts/{sha[:2]}/{sha[2:4]}/{sha}_{name}'


def put_file(path: str, *, owner_user_id: str | None, name: str | None = None,
             run_id: str | None = None, step_id: str | None = None,
             session_id: str | None = None, batch_id: str | None = None,
             relation: str = 'output') -> str | None:
    """登记一个文件进 Artifact Store（内容寻址 + 去重 + ref）。返回 artifact id。"""
    if not path or not os.path.isfile(path):
        return None
    name = (name or os.path.basename(path))[:300]
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    sha = h.hexdigest()
    size = os.path.getsize(path)
    key = _storage_key(sha, name)
    abs_key = os.path.join(os.path.dirname(
        __import__('config').AI_WORKSPACE_ROOT), key) \
        if not os.path.isabs(key) else key
    # storage_key 相对 AI_WORKSPACE_ROOT
    from config import AI_WORKSPACE_ROOT
    abs_key = os.path.join(AI_WORKSPACE_ROOT, key)
    aid = 'art_' + secrets.token_hex(7)
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            # M9 回修：去重按 (sha256, name, owner) 隔离——同用户同内容同名
            # 复用既有行；跨用户不复用（否则复用他人 owner 的行 → 本人下载
            # 403、列表不可见）。物理文件仍按 sha 共享（内容寻址不浪费空间）。
            cur.execute(
                "SELECT id FROM artifacts WHERE sha256=%s AND name=%s "
                "  AND owner_user_id IS NOT DISTINCT FROM %s",
                (sha, name, owner_user_id))
            row = cur.fetchone()
            if row:
                aid = row[0]
            else:
                os.makedirs(os.path.dirname(abs_key), exist_ok=True)
                if not os.path.exists(abs_key):
                    shutil.copy2(path, abs_key)
                cur.execute(
                    "INSERT INTO artifacts (id, owner_user_id, name, "
                    "  media_type, size_bytes, sha256, storage_key) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (aid, owner_user_id, name,
                     mimetypes.guess_type(name)[0], size, sha, key))
            cur.execute(
                "INSERT INTO artifact_refs (id, artifact_id, run_id, step_id, "
                "  session_id, batch_id, relation) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ('ref_' + secrets.token_hex(6), aid, run_id, step_id,
                 session_id, batch_id, relation))
        conn.commit()
    return aid


def ingest_session_outputs(session_id: str, workspace_path: str, *,
                           run_id: str | None = None,
                           step_id: str | None = None,
                           batch_id: str | None = None,
                           owner_user_id: str | None = None) -> list[str]:
    """把会话工作区 outputs/ 收进 Artifact Store（Phase D 集成点）。"""
    out_dir = os.path.join(workspace_path, 'outputs')
    if not os.path.isdir(out_dir):
        return []
    ids = []
    for fn in sorted(os.listdir(out_dir)):
        p = os.path.join(out_dir, fn)
        if os.path.isfile(p):
            aid = put_file(p, owner_user_id=owner_user_id, name=fn,
                           run_id=run_id, step_id=step_id,
                           session_id=session_id, batch_id=batch_id,
                           relation='output')
            if aid:
                ids.append(aid)
    return ids


def get_artifact(artifact_id: str) -> dict | None:
    from db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, owner_user_id, name, media_type, size_bytes, "
                "sha256, storage_key, status, created_at "
                "FROM artifacts WHERE id = %s AND status = 'active'",
                (artifact_id,))
            row = cur.fetchone()
    if not row:
        return None
    cols = ('id', 'ownerUserId', 'name', 'mediaType', 'sizeBytes', 'sha256',
            'storageKey', 'status', 'createdAt')
    out = dict(zip(cols, row))
    if out.get('createdAt') is not None:
        out['createdAt'] = out['createdAt'].isoformat()
    return out


def artifact_path(artifact: dict) -> str | None:
    """storage_key → 绝对路径（存在才返回）。"""
    from config import AI_WORKSPACE_ROOT
    p = os.path.join(AI_WORKSPACE_ROOT, artifact['storageKey'])
    return p if os.path.isfile(p) else None
