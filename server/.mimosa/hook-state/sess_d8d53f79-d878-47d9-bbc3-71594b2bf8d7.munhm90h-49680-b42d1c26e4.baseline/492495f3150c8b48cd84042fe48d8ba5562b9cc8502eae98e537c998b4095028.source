"""Global skill management — disk + DB operations.

Central storage: <AI_WORKSPACE_ROOT>/global-skills/<name>/
(same root chat sessions use — batch children inject from here too)
DB table: global_skills (id, name, description, enabled, uploaded_by, file_size)

Symlink injection into session workspaces is handled by batch_engine._inject_global_skills.
"""
import os
import re
import shutil
import tempfile
import uuid
import zipfile

from psycopg2.extras import RealDictCursor

from db import get_db
from utils.zip_unicode import safe_extract_decoded

SKILL_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
MAX_SKILL_ZIP_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_ZIP_ENTRIES = 200

# 平台同步到 OC 全局目录的副本的标记文件（无符号链接权限时的复制兜底），
# 禁用清理只认这个标记——与用户自装同名目录区分开。
PLATFORM_SYNC_MARKER = '.platform-synced'


def global_skills_root(workspace_root: str) -> str:
    return os.path.join(workspace_root, 'global-skills')


def _extract_skill_name_from_md(md_path: str) -> str | None:
    """Extract name from SKILL.md YAML frontmatter."""
    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read(4096)
    except OSError:
        return None
    if not content.startswith('---'):
        return None
    end = content.find('---', 3)
    if end < 0:
        return None
    for line in content[3:end].splitlines():
        if line.startswith('name:'):
            return line.split(':', 1)[1].strip().strip('"').strip("'")
    return None


def validate_skill_zip(zip_path: str) -> tuple[str | None, str | None]:
    """Validate a skill zip file.

    Returns (skill_name, error_message). If valid, error_message is None.
    If invalid, skill_name is None and error_message describes the problem.
    """
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            entries = zf.namelist()
            if len(entries) > MAX_ZIP_ENTRIES:
                return None, f'zip 包含 {len(entries)} 个条目，超过 {MAX_ZIP_ENTRIES} 的上限'

            # Find SKILL.md
            skill_md = None
            for name in entries:
                if name.endswith('SKILL.md') and '/' in name:
                    skill_md = name
                    break
                if name == 'SKILL.md':
                    skill_md = name
                    break
            if not skill_md:
                return None, 'zip 中未找到 SKILL.md 文件'

            # Extract name from frontmatter
            with zf.open(skill_md) as f:
                content = f.read(4096).decode('utf-8', errors='replace')
            skill_name = None
            if content.startswith('---'):
                end = content.find('---', 3)
                if end > 0:
                    for line in content[3:end].splitlines():
                        if line.startswith('name:'):
                            skill_name = line.split(':', 1)[1].strip().strip('"').strip("'")
                            break

            # Fallback: use zip filename
            if not skill_name:
                skill_name = os.path.splitext(os.path.basename(zip_path))[0]

            if not SKILL_NAME_RE.match(skill_name):
                return None, f'skill 名称 "{skill_name}" 不合法（需匹配 {SKILL_NAME_RE.pattern}）'

            return skill_name, None
    except zipfile.BadZipFile:
        return None, '不是有效的 zip 文件'


def install_skill_from_zip(zip_path: str, description: str,
                           uploaded_by: str, workspace_root: str) -> dict:
    """Install a global skill from a zip file.

    Returns the created skill dict, or raises ValueError on validation failure.
    """
    if os.path.getsize(zip_path) > MAX_SKILL_ZIP_BYTES:
        raise ValueError(f'zip 文件超过 {MAX_SKILL_ZIP_BYTES // 1024 // 1024} MB 的上限')

    skill_name, err = validate_skill_zip(zip_path)
    if err:
        raise ValueError(err)

    root = global_skills_root(workspace_root)
    dest_dir = os.path.join(root, skill_name)

    # Check uniqueness
    if os.path.exists(dest_dir):
        raise ValueError(f'全局 skill "{skill_name}" 已存在')

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM global_skills WHERE name = %s", (skill_name,))
            if cur.fetchone():
                raise ValueError(f'全局 skill "{skill_name}" 已存在（数据库记录）')

    # Extract to temp dir, then move (atomic)
    os.makedirs(root, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(dir=root, prefix='.tmp-')
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            safe_extract_decoded(zf, tmp_dir)

        # Find the extracted content — may be in a subdirectory
        # e.g. zip contains test-skill/SKILL.md → extractall gives tmp/test-skill/SKILL.md
        # We want dest_dir/SKILL.md, not dest_dir/test-skill/SKILL.md
        extracted = tmp_dir
        entries = os.listdir(tmp_dir)
        if len(entries) == 1 and os.path.isdir(os.path.join(tmp_dir, entries[0])):
            # Single top-level directory — use its contents directly
            extracted = os.path.join(tmp_dir, entries[0])

        # Calculate total file size
        total_size = 0
        for dirpath, _, filenames in os.walk(extracted):
            for fn in filenames:
                total_size += os.path.getsize(os.path.join(dirpath, fn))

        # Move to destination
        os.rename(extracted, dest_dir)

        # DB record
        skill_id = f'gs-{uuid.uuid4().hex[:12]}'
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO global_skills (id, name, description, uploaded_by, file_size) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING *",
                    (skill_id, skill_name, description, uploaded_by, total_size),
                )
                row = dict(cur.fetchone())
            conn.commit()

        return row
    finally:
        # Clean up temp dir if it still exists
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def list_global_skills() -> list[dict]:
    """List all global skills from DB."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT g.*, u.username AS uploader_name "
                "FROM global_skills g "
                "LEFT JOIN users u ON u.id = g.uploaded_by "
                "ORDER BY g.name")
            return [dict(r) for r in cur.fetchall()]


def get_global_skill(skill_id: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT g.*, u.username AS uploader_name "
                "FROM global_skills g "
                "LEFT JOIN users u ON u.id = g.uploaded_by "
                "WHERE g.id = %s", (skill_id,))
            row = cur.fetchone()
    return dict(row) if row else None


def get_global_skill_by_name(name: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM global_skills WHERE name = %s", (name,))
            row = cur.fetchone()
    return dict(row) if row else None


def update_global_skill(skill_id: str, *, description: str | None = None,
                        enabled: bool | None = None) -> dict | None:
    """Update description and/or enabled flag. Returns updated row or None."""
    sets, params = [], []
    if description is not None:
        sets.append("description = %s")
        params.append(description)
    if enabled is not None:
        sets.append("enabled = %s")
        params.append(enabled)
    if not sets:
        return get_global_skill(skill_id)
    sets.append("updated_at = now()")
    params.append(skill_id)
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"UPDATE global_skills SET {', '.join(sets)} WHERE id = %s RETURNING *",
                tuple(params))
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def delete_global_skill(skill_id: str, workspace_root: str) -> bool:
    """Delete skill from DB and disk. Returns True if found."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT name FROM global_skills WHERE id = %s", (skill_id,))
            row = cur.fetchone()
            if not row:
                return False
            name = row['name']
            cur.execute("DELETE FROM global_skills WHERE id = %s", (skill_id,))
        conn.commit()
    # Delete from disk
    skill_dir = os.path.join(global_skills_root(workspace_root), name)
    if os.path.isdir(skill_dir):
        shutil.rmtree(skill_dir, ignore_errors=True)
    return True


def list_skill_files(skill_id: str, workspace_root: str) -> list[dict]:
    """List files inside a global skill directory."""
    skill = get_global_skill(skill_id)
    if not skill:
        return []
    skill_dir = os.path.join(global_skills_root(workspace_root), skill['name'])
    if not os.path.isdir(skill_dir):
        return []
    files = []
    for dirpath, _, filenames in os.walk(skill_dir):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, skill_dir).replace(os.sep, '/')
            files.append({
                'name': fn,
                'path': rel,
                'size': os.path.getsize(fp),
            })
    files.sort(key=lambda f: f['path'])
    return files


def read_skill_file(skill_id: str, file_path: str,
                    workspace_root: str, max_bytes: int = 256 * 1024) -> dict | None:
    """Read a file from a global skill. Returns {content, truncated, binary} or None."""
    skill = get_global_skill(skill_id)
    if not skill:
        return None
    skill_dir = os.path.join(global_skills_root(workspace_root), skill['name'])
    normalized = os.path.normpath(file_path)
    if '..' in normalized.split(os.sep):
        return None
    abs_path = os.path.join(skill_dir, normalized)
    if not os.path.commonpath([skill_dir, abs_path]).startswith(skill_dir):
        return None
    if not os.path.isfile(abs_path):
        return None
    try:
        with open(abs_path, 'rb') as f:
            raw = f.read(max_bytes + 1)
    except OSError:
        return {'content': '', 'truncated': False, 'binary': False}
    if b'\x00' in raw:
        return {'content': '', 'truncated': False, 'binary': True}
    truncated = len(raw) > max_bytes
    content = raw[:max_bytes].decode('utf-8', errors='replace')
    return {'content': content, 'truncated': truncated, 'binary': False}


def sync_platform_skills_to_oc_global(oc_global_dir: str, workspace_root: str,
                                      skills: list[dict] | None = None) -> list[str]:
    """把启用的平台技能同步为 OC 全局技能链接。

    在 <oc_global_dir>/skills/<name> 建链接 → <workspace_root>/global-skills/<name>；
    已禁用的平台技能若在 OC 全局目录留有链接则移除（只清指向平台目录的链接，
    用户自装技能不动）。幂等；返回本次确保可用的技能名列表。

    为什么不往 <workspace>/.opencode/skills 注入（历史实现）：workspace 一旦
    存在 .opencode 目录，OC 实例引导就会在其中执行 `bun add @opencode-ai/plugin`
    —— 该安装走用户 .npmrc 配置的网络（本机为慢速 socks 隧道），每次新建
    会话固定阻塞 70s~数分钟后才失败放行（create_session 慢的根因，2026-09-28
    实测：目录无 .opencode 时建会话 0.98s，有 .opencode 时 74s+）。改挂 OC
    全局 skills 目录后 workspace 不再出现 .opencode，OC 对全局 skills 的
    发现与 workspace 注入等效。
    """
    if not oc_global_dir:
        return []
    if skills is None:
        skills = list_global_skills()
    enabled = [s for s in (skills or []) if s.get('enabled')]
    gs_root = os.path.abspath(global_skills_root(workspace_root))
    if enabled and not os.path.isdir(gs_root):
        return []
    oc_skills_dir = os.path.join(oc_global_dir, 'skills')

    synced: list[str] = []
    try:
        os.makedirs(oc_skills_dir, exist_ok=True)
        for s in enabled:
            name = s['name']
            src = os.path.join(gs_root, name)
            if not os.path.isdir(src):
                continue
            dst = os.path.join(oc_skills_dir, name)
            try:
                if os.path.islink(dst) and os.path.realpath(dst) == os.path.realpath(src):
                    synced.append(name)
                    continue  # 链接已就位
                if os.path.islink(dst):
                    os.remove(dst)  # 旧链接指向变了 → 重建
                elif os.path.exists(dst):
                    # 同名实体目录：平台同步副本（带标记）或用户自装 → 视为可用
                    synced.append(name)
                    continue
                os.symlink(src, dst)
                synced.append(name)
            except OSError:
                # Windows 无符号链接权限 → 复制兜底（带标记，便于禁用时清理）
                try:
                    if os.path.islink(dst):
                        os.remove(dst)
                    if os.path.isdir(dst) and not os.path.exists(
                            os.path.join(dst, PLATFORM_SYNC_MARKER)):
                        synced.append(name)  # 用户自装实体目录 → 视为可用
                        continue
                    if not os.path.isdir(dst):
                        shutil.copytree(src, dst)
                    with open(os.path.join(dst, PLATFORM_SYNC_MARKER), 'w',
                              encoding='utf-8') as f:
                        f.write(src)
                    synced.append(name)
                except Exception:  # noqa: BLE001
                    pass
        # 清理已禁用平台技能的残留：链接（指向平台目录）或带标记的同步副本
        for s in (skills or []):
            if s.get('enabled'):
                continue
            name = s['name']
            dst = os.path.join(oc_skills_dir, name)
            try:
                if os.path.islink(dst) and os.path.realpath(dst).startswith(
                        os.path.realpath(gs_root) + os.sep):
                    os.remove(dst)
                elif os.path.isdir(dst) and not os.path.islink(dst) and \
                        os.path.exists(os.path.join(dst, PLATFORM_SYNC_MARKER)):
                    shutil.rmtree(dst, ignore_errors=True)
            except OSError:
                pass
    except Exception:  # noqa: BLE001 —— 同步失败不阻断调用方
        return synced
    return synced


def inject_global_skills(workspace_path: str,
                         workspace_root: str | None = None) -> list[str]:
    """保证平台技能对 OC 可用（历史：往 workspace .opencode/skills 注入，
    现改为同步到 OC 全局 skills 目录——workspace 里出现 .opencode 会触发
    OC 实例引导的 npm 依赖安装，在慢速网络下把 create_session 拖慢 70s+，
    见 sync_platform_skills_to_oc_global 的说明）。

    保留原签名：ai_chat.create_session 与 batch_engine._prepare_workspace
    两个调用点无需改动。workspace_path 参数已不使用（兼容保留）。
    """
    from utils.opencode_launch import global_dir as _oc_global_dir

    if workspace_root is None:
        parts = workspace_path.replace('\\', '/').rstrip('/').split('/')
        if len(parts) < 3:
            return []
        workspace_root = '/'.join(parts[:-2])
    return sync_platform_skills_to_oc_global(_oc_global_dir(), workspace_root)


def inject_single_skill(workspace_path: str, skill_name: str,
                        workspace_root: str | None = None) -> str:
    """确保单个平台全局技能对 OC 可用 — used by the
    trace-analysis route (execution-audit Spec §7.1: the analysis session must
    not blanket-inject every enabled skill, and a missing/injected-failed
    trace-analyzer must FAIL the analysis instead of degrading silently).

    Returns the skill's source directory. Raises FileNotFoundError when the
    skill does not exist in platform storage; OSError when injection fails.
    同步目标现为 OC 全局 skills 目录（原 workspace .opencode/skills 注入会
    触发 OC 引导的 npm 安装拖慢建会话，见 sync_platform_skills_to_oc_global）。
    """
    if workspace_root is None:
        parts = workspace_path.replace('\\', '/').rstrip('/').split('/')
        if len(parts) < 3:
            raise FileNotFoundError(f'无法从 {workspace_path} 推导技能库根目录')
        workspace_root = '/'.join(parts[:-2])
    workspace_root = os.path.abspath(workspace_root)
    src = os.path.join(global_skills_root(workspace_root), skill_name)
    if not os.path.isdir(src):
        raise FileNotFoundError(f'平台技能 {skill_name} 不存在: {src}')
    from utils.opencode_launch import global_dir as _oc_global_dir

    synced = sync_platform_skills_to_oc_global(
        _oc_global_dir(), workspace_root,
        skills=[{'name': skill_name, 'enabled': True}])
    if skill_name not in synced:
        raise OSError(f'技能 {skill_name} 同步到 OC 全局目录失败')
    return src


BUILTIN_HASH_MARKER = '.builtin-hash'


def _dir_hash(dirpath: str) -> str:
    """目录内容哈希;跳过 .builtin-hash marker 本身,否则 marker 内容会改变
    目录哈希,导致"存下的哈希永远对不上"。"""
    import hashlib
    h = hashlib.sha1()
    for root, _, fns in sorted(os.walk(dirpath)):
        for fn in sorted(fns):
            if fn == BUILTIN_HASH_MARKER:
                continue
            fp = os.path.join(root, fn)
            h.update(os.path.relpath(fp, dirpath).encode('utf-8'))
            with open(fp, 'rb') as f:
                h.update(f.read())
    return h.hexdigest()[:16]


def _refresh_builtin(dest_dir: str, description: str,
                     repo_skills_dir: str | None = None) -> bool:
    """内置技能内容刷新:仓库版本变化且安装副本未被改动时,替换目录并更新
    DB 描述。安装副本被管理员改过(marker 记录的哈希对不上当前内容)则保持
    原样;仓库与安装一致时是幂等 no-op。返回是否发生了刷新。
    无 marker 的存量安装(早期版本装的)一律采纳刷新——这些版本只能出自
    仓库,不存在 marker 之前的"管理员定制"记录;此后管理员改动会得到保护。
    repo_skills_dir 供测试注入,缺省为仓库 skills/ 下同名技能目录。"""
    marker = os.path.join(dest_dir, BUILTIN_HASH_MARKER)
    repo_dir = repo_skills_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', '..', 'skills',
        os.path.basename(dest_dir))
    if not os.path.isdir(repo_dir):
        return False
    installed_hash = None
    if os.path.isfile(marker):
        with open(marker, encoding='utf-8') as f:
            installed_hash = f.read().strip()
        current = _dir_hash(dest_dir)
        if current != installed_hash:
            return False  # 管理员改过,不动
        if current == _dir_hash(repo_dir):
            return False  # 仓库无更新,幂等跳过
    shutil.rmtree(dest_dir)
    shutil.copytree(repo_dir, dest_dir)
    with open(marker, 'w', encoding='utf-8') as f:
        f.write(_dir_hash(dest_dir))
    with get_db() as conn:
        with conn.cursor() as cur:
            total_size = sum(
                os.path.getsize(os.path.join(dp, fn))
                for dp, _, fns in os.walk(dest_dir) for fn in fns)
            cur.execute(
                "UPDATE global_skills SET description=%s, file_size=%s, "
                "  updated_at=now() WHERE name=%s",
                (description, total_size, os.path.basename(dest_dir)))
        conn.commit()
    return True


def ensure_builtin_skills(workspace_root: str | None = None) -> list[str]:
    """把仓库 skills/ 目录下的内置技能同步进全局技能(应用启动时调用)。

    只插入缺失的技能(按 name 判断),绝不覆盖被管理员修改过的版本(见
    _refresh_builtin);仓库内容有更新且安装副本未被改动时会地刷新。
    global-skills 目录已有文件而缺 DB 行时,就地复用目录只补建行。
    返回本次新装的名字列表。仓库 skills/ 是内置技能的唯一事实来源:
    部署拉代码后重启,即自带这批技能,无需手工上传。"""
    from config import AI_WORKSPACE_ROOT

    repo_dir = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', '..', 'skills'))
    if not os.path.isdir(repo_dir):
        return []

    root = global_skills_root(workspace_root or AI_WORKSPACE_ROOT)
    os.makedirs(root, exist_ok=True)

    known = {s['name'] for s in list_global_skills()}
    installed = []
    refreshed = []
    for entry in sorted(os.listdir(repo_dir)):
        skill_md = os.path.join(repo_dir, entry, 'SKILL.md')
        if not os.path.isfile(skill_md):
            continue
        try:
            with open(skill_md, encoding='utf-8', errors='replace') as f:
                head = f.read(4096)
        except OSError:
            continue
        m_name = re.search(r'^name:\s*(.+)$', head, re.M)
        m_desc = re.search(r'^description:\s*(.+)$', head, re.M)
        name = (m_name.group(1).strip() if m_name else entry)
        description = (m_desc.group(1).strip() if m_desc else '')[:200]
        db_known = name in known
        dest_dir = os.path.join(root, name)

        if os.path.isdir(dest_dir):
            # 目录在:未被管理员改动时随仓库刷新(内置技能唯一事实来源是仓库);
            # DB 行缺失时继续走下面的补建行
            if _refresh_builtin(dest_dir, description):
                refreshed.append(name)
        elif not db_known:
            shutil.copytree(os.path.join(repo_dir, entry), dest_dir)
            with open(os.path.join(dest_dir, BUILTIN_HASH_MARKER), 'w',
                      encoding='utf-8') as f:
                f.write(_dir_hash(dest_dir))
        if db_known:
            continue

        total_size = sum(
            os.path.getsize(os.path.join(dirpath, fn))
            for dirpath, _, fns in os.walk(dest_dir) for fn in fns)
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO global_skills (id, name, description, enabled, "
                    "  file_size, created_at, updated_at) "
                    "VALUES (%s, %s, %s, TRUE, %s, now(), now()) "
                    "ON CONFLICT (name) DO NOTHING RETURNING name",
                    (str(uuid.uuid4()), name, description, total_size),
                )
                if cur.fetchone():
                    installed.append(name)
    if refreshed:
        print(f'builtin skills refreshed from repo: {", ".join(refreshed)}')
    return installed
